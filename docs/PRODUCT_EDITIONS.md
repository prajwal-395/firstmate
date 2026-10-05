# Product editions

Ren has two build editions. `personal` is the default for a checkout and
keeps the captain's current component choices. `public` is selected with
`ren package-engine --edition public`; the immutable build record carries
that choice. `REN_EDITION` is only an override for an unbuilt checkout and
cannot change an installed build.

## Component policy

`THIRD_PARTY_NOTICES` is the component registry. Every component row has a
stable `component-id` and an `edition` value of `public`, `personal-only`, or
`public-optional`. A public build omits personal-only `package-path` and
`installer-path` entries and filters personal-only and public-optional
`requirement-ref` packages from the shipped requirements and lock files.
Public asset `package-copy` rows describe files that must be staged outside a
renderer directory that the public build omits.

`public-optional` marks a component whose engine code is shipped in both
editions but whose pip dependency is not bundled in a public build. The user
fetches and installs it separately; when absent, the capability reports
unavailable and the pipeline continues without it.

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

| Capability | Personal provider | Public provider | Selector / loader |
| --- | --- | --- | --- |
| Face identity | `model.insightface_buffalo_l` | (none qualified) | `person_entity._face_app` |
| Graphics rendering | `renderer.remotion` | `renderer.hyperframes` | `graphics_renderer.resolve_engine` |
| SFX profiling | `model.audio_flamingo_next` | `model.laion_clap` | `sfx_pipeline.load_afnext_model` |
| Prosody | `python.praat_parselmouth` | `python.praat_parselmouth` (public-optional) | `speech_advanced_pipeline.analyze_prosody` |

For the face alternative, no public provider has qualified. For SFX, LAION-CLAP
(Apache-2.0) is the public provider: it outputs AudioSet-style labels rather
than free-text captions, qualified in
`docs/evidence/public_audio_alternatives.md`. For prosody, praat-parselmouth
(GPLv3) is public-optional: the public build does not bundle it, the user
installs it separately, and the pipeline continues without prosody when it is
absent.

`ren doctor` reports the selected edition and the renderer route. It remains
read-only: HyperFrames is fetched by the public render path after checking
the edition gate.
