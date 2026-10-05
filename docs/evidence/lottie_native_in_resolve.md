# Lottie native in Resolve - follow-up findings

Task: `vep-lottie-native-in-resolve`. Date: 2026-10-04. Worker: firstmate crewmate (disposable worktree).
Follows the scout report `data/vep-lottie-native-in-resolve/report.md` (2026-09-21) and does its three follow-ups.

## Verdict

**A script can reach Resolve 21's native Lottie support for place-and-transform, and the vector route is dramatically cleaner than composing rendered frames. The speed half is not measured - the machine never went idle.**

- **Place-and-transform: YES, re-confirmed.** A `.lottie` imports as a media-pool item and places as a `type=video` timeline clip with the full 25-key generic transform set; `SetProperties({"ZoomX": 1.25})` returns `True` and the read-back holds. No Lottie-specific scripting entry exists and none is needed for this.
- **Cleaner: YES, structurally and by size.** The same 90-frame progress bar is **465 bytes** as `.lottie` versus **29,619 bytes** as a tightly-cropped `qtrle` (640x48) and **697,571 bytes** as a full-frame 1920x1080 `qtrle`. The `.lottie` is resolution-independent (scales to any delivery format for free), carries native alpha (no `Data Level` trap - the raster-import defect `overlay_carriage.py` exists to fix), and is vector (no 8-bit ProRes DCT ringing, the 0.77-mean deviation the carriage doc measured).
- **Faster: NOT MEASURED.** The heavy render comparison (Chromium render + qtrle transcode + import vs `.lottie` authoring + import, plus export time and pixel cleanliness) needs an idle machine and the captain's window. The load average stayed between 23 and 54 all evening with 19+ concurrent opencode workers; the scout's report explicitly warns "Do not start it under tonight's load." Deferred, not skipped.
- **OGraf Title route: NOT scriptable in a running session.** A runtime-copied OGraf template is not in Resolve's indexed title list (scanned at startup), so `InsertFusionTitleIntoTimeline` / `InsertTitleIntoTimeline` return `False` for every name variant. The schema-params-through-`GetProperties` question is therefore moot for the scripting route.

## 1. OGraf Title-route probe (follow-up 2)

Captain's board 2026-10-04 allowed this probe. Copied the shipped `Breaking-News` example (`Breaking-News.js` + `Breaking-News.ograf.json`, 14 schema properties: 7 text + 7 color) into the per-user `Titles/OGraf/` dir, then into the pre-existing `HTML Titles/Ograf/` dir, and tried to place it in a scratch project.

| Step | Result |
|---|---|
| Copy to `Titles/OGraf/` | 2 files, verified on disk |
| Copy to `HTML Titles/Ograf/` | 2 files, verified on disk |
| `InsertFusionTitleIntoTimeline` x 6 name variants | all `False` |
| `InsertTitleIntoTimeline` x 6 name variants | all `False` |
| Control: `InsertFusionTitleIntoTimeline("ZZZ-Definitely-Not-A-Title")` | `False` |
| `SetCurrentTimeline` before placing | `True`, did not help |
| Wait 5s after copy (file-watcher test) | no effect |

Name variants tried: `Breaking-News`, `Breaking News Broadcast` (the manifest `name`), `modern-breaking-news-v2` (the manifest `id`), `OGraf/Breaking-News`, `Titles/OGraf/Breaking-News`, `OGraf Breaking-News`.

**The control is the finding.** A definitely-nonexistent name returns `False` exactly like the OGraf template, so the methods behave correctly - the runtime-copied template is simply not in Resolve's indexed list. Resolve scans the Titles directories at startup; a runtime copy is not picked up without a restart. The OGraf docs (`02-Resolve-Integration.md`) describe the web-component lifecycle ("when a user adds an OGraf title") but offer no scripting path, and the 21.1 stub has no OGraf entry (confirmed: `grep -i ograf` over `DaVinciResolveScript.pyi` returns nothing).

**Consequence:** the OGraf Title route - the one with real content parameters - cannot be scripted in a running Resolve session. The `schema.properties`-become-Inspector-controls behaviour is real (per `05-Properties-and-Controls.md`) but unreachable without a restart. Cleanup verified: the `OGraf` folder was deleted and the Titles dir confirmed back to its prior contents (only `HTML Titles`). Captain's project restored to `Podcast (field test)`.

## 2. Lottie pilot on the two vector classes (follow-up 3)

Authored two `.lottie` files (Bodymovin JSON zipped to `.lottie` layout) for the two vector classes the scout's report names as Lottie candidates, then placed them through the proven calls on a scratch project.

| Artefact | Bytes | Import | Place | Transform |
|---|---:|---|---|---|
| `progress_bar.lottie` (track + fill advancing 0->100% over 90f) | 465 | 1 item | `type=video`, dur=120f | 25 keys, `SetProperties(Zoom 1.25)` -> `True`, read-back holds |
| `transition_bumper.lottie` (star scaling 0->100->0 + rotating over 30f) | 513 | 1 item | `type=video`, dur=120f | 25 keys, `SetProperties(Zoom 1.25)` -> `True`, read-back holds |

Both import via the plain-string `ImportMedia([path])` shape (the pipeline's working shape, `resolve_build_timeline.py:499,1695`) and place via `AppendToTimeline([{"mediaPoolItem": item}])`. Both cut as normal video items with the full generic transform set.

### Size comparison (the "cleaner" argument, no render needed)

Same content (the progress bar, 90 frames @ 30fps) both ways:

| Route | Bytes | Note |
|---|---:|---|
| `.lottie` (vector) | **465** | resolution-independent |
| `qtrle` cropped to 640x48 tight box | 29,619 | the pipeline's real tight-box crop |
| `qtrle` full-frame 1920x1080 | 697,571 | uncropped upper bound |

The `.lottie` is **64x smaller** than the tightly-cropped `qtrle` and **~1500x smaller** than the full-frame one, and it scales to any delivery format for free. The `qtrle` is fixed at the rendered resolution.

### Structural cleanliness (why the composite check would pass)

The scout's report names two bankable "cleaner" arguments that need no render, and both hold:

1. **No `Data Level` trap.** The `Auto`-reads-video-range defect (`overlay_carriage.py:54-64`, measured: `qtrle`+`Auto` composites 109.331 where the plate is 127.957, black crushed by -18.6) is a raster-import defect. A `.lottie` clip carries native alpha; there is no 8-bit RGBA import to misread.
2. **No DCT ringing.** The `qtrle`-vs-ProRes measurement (composited over a plate, mean 0.0000 for `qtrle` vs 0.77 for ProRes 4444) is a lossy-codec artefact. A `.lottie` is vector; there is no lossy codec in the path.

The `assert_transparent_region_unchanged` check (`overlay_carriage.py:404`) is the real composite gate, and it is format-blind (it measures the composited frame against the plate wherever the overlay's alpha is zero). Running it on a `.lottie` composite is part of the deferred heavy render below.

## 3. Heavy render comparison (follow-up 1) - DEFERRED

The captain's actual question (the speed half) is the heavy render comparison. It was not run.

**Why:** the machine never went idle. Load average stayed between 23 and 54 all evening (21:52-22:15), with 19+ concurrent opencode workers, a caption worker waiting on the captain's `Podcast (field test)` project, and another crewmate running a Resolve hang-guard task. The scout's report is explicit: the comparison "saturates cores. Do not start it under tonight's load." Starting a Chromium render + qtrle transcode + export under load would interfere with the other workers and produce meaningless timings.

**What it must measure** (specified so it starts cold, per the scout's report section 5):
- (a) wall time: Chromium render + qtrle transcode + import vs `.lottie` authoring + import
- (b) export time of the finished timeline
- (c) pixel cleanliness: alpha edges, scaling to a second delivery format, colour drift vs source

**Partial numbers already measured** (local, no Resolve render):
- `.lottie` authoring: the generator script produced both files in well under 1s
- `qtrle` transcode of 90 PNG frames: 1.16s (system ffmpeg, `-c:v qtrle -pix_fmt argb`)
- The missing piece is the Chromium render time and the Resolve export time, both of which need the idle machine.

## Boundaries held

- All Resolve work in `VEPLOTTIE-*` scratch projects, all deleted; captain's project restored to `Podcast (field test)` after every probe and verified.
- Every Resolve call ran under a deadline (`resolve_deadline.call_with_deadline`) and the connection under the Ren broker lease (`resolve_lease`), per the brief. No call hung; no exact call to report.
- No project or timeline resolution changed. No `--accept-editor-changes` or any override. No marker writes. No renders or exports.
- OGraf folder cleanup verified against a prior snapshot (only `HTML Titles` remains).
- Probe scripts live in `/tmp/fm-vep-lottie-native-in-resolve/` (outside the worktree).
