# Product editions

Ren has two build editions. `personal` is the default for a checkout and
keeps the captain's current component choices. `public` is selected with
`ren package-engine --edition public`; the immutable build record carries
that choice. `REN_EDITION` is only an override for an unbuilt checkout and
cannot change an installed build.

## Component policy

`THIRD_PARTY_NOTICES` is the component registry. Every component row has a
stable `component-id` and an `edition` value of `public` or
`personal-only`. A public build omits personal-only `package-path` and
`installer-path` entries and filters personal-only `requirement-ref`
packages from the shipped requirements and lock files. Public asset
`package-copy` rows describe files that must be staged outside a renderer
directory that the public build omits.

Runtime code calls `ren.edition.require_component` before a component is
loaded or fetched. Providers that have both implementations call
`ren.edition.select_component(capability, personal_component=...,
public_component=...)`: personal prefers its personal-only implementation
and falls back to the public implementation; public considers only the
public implementation. Add the public component's notice row before
pointing a selector at it. A missing public provider refuses by capability
name rather than touching the personal component.

## Public provider seams

The alternatives being qualified plug in at these call sites:

| Capability | Personal provider | Selector / loader |
| --- | --- | --- |
| Face identity | `model.insightface_buffalo_l` | `person_entity._face_app` |
| Graphics rendering | `renderer.remotion` | `graphics_renderer.resolve_engine`; HyperFrames is already the public renderer |
| SFX profiling | `model.audio_flamingo_next` | `sfx_pipeline.load_afnext_model` |
| Prosody | `python.praat_parselmouth` | `speech_advanced_pipeline.analyze_prosody` |

For the face, SFX and prosody alternatives, register the qualified public
component in `THIRD_PARTY_NOTICES`, replace the `public_component=None`
argument at the named selector, and dispatch to its loader. Keep the
personal provider first in the personal edition. The Remotion gates in
`remotion_batch`, subtitle rendering, motion graphics, bookends, timed text
and reel headers prevent a public build from reaching an unsupported
personal-only renderer fallback.

`ren doctor` reports the selected edition and the renderer route. It remains
read-only: HyperFrames is fetched by the public render path after checking
the edition gate.
