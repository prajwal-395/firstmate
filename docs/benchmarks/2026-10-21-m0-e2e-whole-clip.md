# M0 end to end, from the WHOLE clip: the car keys

**Lane:** `g-m0-e2e-whole-clip` · **Date:** 2026-10-21 ·
**Engine base:** `d6c3f9ce7` (main) plus `0e478411f` (the linked exact-frame cut, PR
https://github.com/prajwal-395/video_editing_pilot/pull/3104, still open) ·
**Fixture:** the whole 85.82 s take of Apple 001 `IMG_1822`, on a scratch copy.

**Question this lane answered:** the M0 baseline froze its scenario on a *wrong*
object. The captain checked the clip and answered, verbatim:

> yeah those are carkeys in his hand so not a device like a phone if that was the
> assumption. he just kinda got em in his hand as he's trying to explain something.
> so in particular i woudn't call this a failure really because the system was still
> able to pick a range and estimate that is vaugely correct to the question even
> though the question itself was not correct. it's less impressive if all it had to
> do was look through a range of like 20-30 frames compared to if it isolated those
> frames and picked a frame from the whole video or whole clip

So the scenario is re-framed around what the clip actually shows — **car keys held
while he talks** — and the chain is run **from the whole clip with no pre-selected
window**, because isolating the frames from the whole take is the part the captain
says is impressive.

**Short answer:** the chain runs end to end on the whole clip, and it finds the car
keys in it (95 detections over 50 of 86 inspected frames). It does **not** name the
occurrence the ground truth names, because the coarse candidate class the scenario
asks for collapses the six by-eye arrivals into ONE 64.8-second span on a take whose
speaker holds the keys near his face throughout. The frame-exact refinement can name
the arrival of the right occurrence to **0 frames** (the rule shipped here) where the
rule it offered named the deepest frame of the hold, **23 frames late** — and **225
frames late** (inside the *next* hold) on the occurrence the model named. The cut
itself was **not placed by this lane**: the machine's scratch-origin slot was held
by another lane's live run for the whole window (§4), and the operation is measured
by PR 3104 (24 live cuts, exact placement, the audio item unchanged).

---

## 1. The scenario, corrected

| | The M0 baseline froze this | This lane freezes this |
|---|---|---|
| The object | "the handheld device" (the caption's own reading) | **the car keys** (the captain's answer; the black fob with the teal band) |
| The event | "the second time the person brings the handheld device to their mouth" | **the second time the speaker brings the car keys to his lips** |
| The clip | the first 48.0 s of the take, re-encoded | **the whole 85.82 s take**, byte-for-byte |
| The window | the coarse sample's own hit | **none selected in advance** |
| The truth | agent-adjudicated band 33.30–33.70 s (±6 frames), provisional | **agent-adjudicated frames**, written down below; the target arrival adjudicated at 30 fps (±2 frames) |

The keys are never released: he holds them for the whole take. So the event is not a
pickup, and the scenario says so — it is the repeated **arrival of the keys at his
lips**, which the footage shows six times.

### 1.1 The fixture

| | |
|---|---|
| Source (READ ONLY, never written) | `/Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001/raw/IMG_1822.MOV` — Apple 001, `IMG_1822`, 85.82 s, 2574 frames @ 30 fps, 1920×1080, AAC 48 kHz stereo + an `apple_apac` spatial track |
| Scratch copy | `/tmp/m0-e2e/media/IMG_1822_keys_take.MOV` — a **byte copy** (`md5 f3af6808c314cadefc15218baf273037`, identical to the source) |
| Why a byte copy and not a re-encode | the baseline's re-encode (`libx264`, default GOP) destroyed the source's I-frame cadence: its M2 sample landed on **11 frames in 48 s** (0.23 Hz). The source's own I-frames are every 29 frames, so a byte copy gives the coarse lane **89 samples over 85.82 s (1.03 Hz)** — the cadence the engine would see on this take in production |
| Project | `/tmp/m0-e2e/projects/m0-keys` (`PIPELINE_PROJECTS_ROOT` pointed at the scratch root), `ren new m0-keys --fps 30 --footage /tmp/m0-e2e/media --no-speakers --shape picture-led` |
| Catalog | `clip_001`, 85.82 s, 30 fps, 1920×1080, AAC stereo selected (the spatial track is refused as not a separate microphone feed) |
| Preflight | `ren edit m0-keys --only scan --only catalog --only temporal_index --auto --full-auto agent` → scan, catalog and the temporal index (49 energy peaks, 148 high-motion samples, dominant motion `pan_left`) all produced output; **23 min wall** for 3 min of CPU, because the machine is shared |

Nothing in the captain's projects, timelines or media was touched; no broker was
started; `ren qualification reset` was not run. Every measurement below is of the
scratch copy, and every artefact lives outside the checkout.

### 1.2 The ground truth, written down by eye

Adjudicated from the picture: 2 fps contact sheets over the whole take, then 10 fps
over 14–24 s and 49.3–51.2 s, then the individual 30 fps frames around each arrival
that the report relies on. **Agent-adjudicated, and still provisional** — the
captain's re-adjudication is still owed, as it was for the baseline.

| Occurrence | the keys reach his lips | source frame | how it was adjudicated |
|---|---|---|---|
| A1 | 15.4 s | ~462 | 10 fps sheet |
| **A2** | **33.40 s** | **1002** | **30 fps frames 996–1005: the fob is below the lips at 1000–1001, at them at 1002, clamped from 1004 (±2 frames)** |
| A3 | 46.0 s | ~1380 | 2 fps sheet, checked against the coarse hit at 46.412 s |
| A4 | 53.0 s | ~1590 | 2 fps sheet |
| A5 | 61.5 s | ~1845 | 2 fps sheet |
| A6 | 82.5 s | ~2475 | 2 fps sheet |

The occurrences separated by less than a second of the keys being away are one
occurrence; the first four arrivals are separated by gaps of 8 s, 12 s, 7 s and 9 s,
so the six rows are six arrivals.

**The target is A2 — the second arrival — because the scenario says "the second
time".** A2 is the only arrival adjudicated at 30 fps; the others carry a ±7-frame
uncertainty from the 2 fps sheets, and the report says which is which.

---

## 2. The chain, stage by stage

### Stage 1 — request-driven object detection (PR 3080)

```sh
bin/ren evidence m0-keys --object "car keys" --requirement presence \
    --acquire --max-cost-seconds 1200
```

* producer **owlv2** — the cheapest producer that can *name* "car keys"
  (`rf_detr_nano` is COCO-fixed and cannot name it, so it is not eligible; asking it
  anyway would answer nothing, and an empty answer reads as "the object is not
  there")
* **86 frames inspected at 1.0 Hz over 0–85.8 s, `unexamined`: none** — the whole
  clip, no window chosen in advance
* **95 detections over 50 of the 86 frames**; the D-01 coverage table's `car keys`
  row is now MEASURED (coverage 0.581), 23 small instances, 15 partial
* cost: **12 min wall** (7.27 s/frame of detection plus one decode pass over the
  source)

**Did it find the event in the whole clip? Yes.** The keys are detected across the
take, and the detections include the target window: 33 s, 34 s, 35 s, 37 s, 38 s.

### Stage 2 — event spans without identity (PR 3061)

```sh
bin/vep -m library.tools.event_spans build /tmp/m0-e2e/projects/m0-keys
```

M7 was built **with no M3b at all** — the identity lane is licence-blocked for a
commercial purpose — and the record says so: `identity.basis = "geometry"`, and the
`speaking`/`on_screen` predicates are recorded as **unavailable**, never as empty
spans. 22 candidate spans over six classes: `hand_near_mouth` 6, `hand_raise` 8,
`object_pickup` 4, `nod` 2, `person_enters` 1, `person_leaves` 1.

**The class the scenario names cannot separate the arrivals.** The hand is within
`HAND_CANDIDATE_MAX_DISTANCE` (1.5 face widths) of the lips on **74 of the 89
coarse frames** of this take — the speaker holds the keys near his face for most of
it — so the six by-eye arrivals collapse into **one span of 64.78 s** (3.385–68.167 s)
plus five short ones. The class that *does* separate them is the hand's **rise**
(`object_pickup`), and it found **4 of the 6 arrivals** (A2, A3, A5, A6), missing A1
and A4 because the hand was already near the face at the coarse frame before the
arrival (`d = 0.505` at 14.503 s, `d = 0.549` at 52.212 s): a lift that is already
under way when the coarse sample catches it is not a lift.

### Stage 3 — occurrence identity (PR 3057)

```sh
bin/ren evidence m0-keys <compiled plan: {"predicate":"nth_event",
                                           "event_class":"object_pickup","nth":2}>
```

The canonical model carries 22 events, each with an occurrence block and a stable
occurrence key (`asset|class|subject|index`). The answer carries the event, the
occurrence, the receipt, the evidence ids and the key:

```json
{"event_id": "event-obs-47909f5c…-m7-0017",
 "occurrence": {"class": "object_pickup", "subject_id": null, "index": 2},
 "occurrence_key": "asset-47909f5c…|object_pickup|-|2",
 "interval": {"start_s": 49.794, "end_s": 51.728},
 "occurrence_receipt": {"format": "occurrence_receipt/1", …}}
```

**Which occurrence?** The engine's **second** `object_pickup` is the ground truth's
**third** arrival (A3): the ordinal is **one behind** the truth, because the coarse
lane missed A1. The engine's own occurrence **1** (32.392–39.160 s) *is* the ground
truth's second arrival (A2) — the identity axis is right, the coarse lane's recall
is what is short.

### Stage 4 — frame-exact refinement (PR 3062)

`library/tools/frame_exact` decodes the interval at the source's own frame cadence
and re-measures the coarse lane's own geometry (the hand-to-lips distance in face
widths).

| What was refined | Rule | Boundary named | Ground truth | Error |
|---|---|---|---|---|
| A2's coarse hit (frame 1015, 33.841 s), ±0.5 s | `closest_approach` (the rule the command line offered) | frame **1025** (34.175 s) | arrival frame 1002 | **23 frames late** |
| the same interval | `arrival` (shipped here) | frame **1002** (33.408 s) | arrival frame 1002 | **0 frames** |
| the occurrence the model NAMED (A3; coarse frame 1507, 50.245 s), ±4.5 s | `closest_approach` | frame **1605** (53.513 s) — inside the **next** hold (A4) | arrival frame ~1380 | **225 frames late** |
| the same interval | `arrival` | frame **1377** (45.912 s) | arrival frame ~1380 | **3 frames late — inside the 3-frame bar**, but A3's own arrival was read off a 2 fps sheet and carries ±7 frames, so this is a 3-frame error against a ±7-frame truth |

The `arrival` rule's contact distance is a DECLARED number
(`CONTACT_DISTANCE_FACE_WIDTHS = 0.25` face widths, measured on this fixture: the
frames in which the hand holds the keys against the lips measure 0.02–0.05 and the
frames of the approach measure 0.13–0.43), and `--contact-distance` moves it. On this
fixture any threshold in 0.2–0.3 face widths lands within 2 frames of the adjudicated
arrival of A2.

The truncation guard works: a window that starts **inside the previous hold** finds
that hold's contact first and reports `boundary_truncated` at frame 1737 instead of
claiming an arrival.

Cost: 31 frames decoded and measured in **8.8 s** (5.5 s decode, 3.2 s measure) for
the one-second window; 135 frames in 47 s for the nine-second window.

### Stage 5 — the exact-frame linked cut (PR 3104)

**NOT EXERCISED IN THIS LANE** — see §4. The operation itself is measured: PR 3104
placed **24 live cuts** on Ren-owned scratch timelines through the serving broker,
24/24 on the exact frame on both axes, 24/24 with the audio item's id, record span,
source span, gain, fades and enablement unchanged, and the decoded PCM around every
cut frame byte-identical before and after; the one thing that does not survive is
the audio's **LINK** to the picture, because the composed cut replaces the picture
item. This lane did not place a cut of its own: the machine's scratch-origin slot
was held by another lane's live run for the whole window, and the current Resolve
project belonged to another live lane, so a cut would have meant moving another
lane's session.

---

## 3. The per-stage table

| # | Stage | What ran | Found the event in the whole clip? | Which occurrence | Boundary error | The cut |
|---|---|---|---|---|---|---|
| 1 | request-driven object detection (3080) | `ren evidence --object "car keys" --acquire` | **yes** — 95 detections, 50/86 frames, 0–85.8 s, `unexamined: none` | n/a (the object, not the event) | n/a | n/a |
| 2 | event spans without identity (3061) | `event_spans build` (no M3b) | **the class asked for does not** — `hand_near_mouth` is one 64.78 s span (74 of 89 coarse frames are "near the mouth"); the rise class (`object_pickup`) finds **4 of the 6** arrivals | n/a | n/a | n/a |
| 3 | occurrence identity (3057) | `nth_event(object_pickup, 2)` | yes | the engine's **2nd** is the truth's **3rd** arrival (A3); the engine's **1st** is the truth's 2nd (A2) — the ordinal is **one behind** | n/a | n/a |
| 4 | frame-exact refinement (3062) | `frame_exact refine` on the coarse hit | yes | — | **0 frames** on A2 with the `arrival` rule (A2's arrival is adjudicated at 30 fps, ±2 frames), **3 frames** on the named occurrence A3 (whose arrival carries ±7 frames); **23** and **225 frames** with the `closest_approach` rule the CLI offered | n/a |
| 5 | exact-frame linked cut (3104) | the composed picture-only cut through the serving broker | yes | the occurrence the boundary came from | — | **not exercised here** — the machine's scratch-origin slot was held by another lane's live run (§4); the operation is measured by PR 3104 (24/24 exact, audio item unchanged, link not preserved) |

---

## 4. The live cut: what this lane could and could not do

The cut needs a **Ren-owned scratch project** with the media on a timeline, and
every scratch-project lifecycle operation on this machine is queued behind ONE
machine resource: the **scratch origin** (the record of which project and timeline
Resolve had open before a scratch session took it). This lane asked for it, and the
engine answered, verbatim:

```
$ bin/resolve-axi project scratch create "Ren Scratch - fm-m0-keys" --apply
error: the scratch origin is held by pid-80904; it is still held, so nothing was moved
help: resolve-axi project scratch --help
```

The holder was another lane's live scratch session (`Ren Scratch - fm-p008`, then
`Ren Scratch - fm-p008c`, then a bench lane's `Ren Scratch - vep-bench-demo2` — the
record was re-written while this lane was waiting, so that lane was still working).
The current Resolve project belonged to another live lane too (`Ren Scratch -
vc004`).

The lifecycle verbs that follow the create were refused outright, and the refusal
names the second reason, which no wait would have fixed:

```
$ bin/resolve-axi project scratch open "Ren Scratch - fm-m0-keys" --apply
REN_REFUSAL ... "not starting the production Resolve broker": "this client runs
from a linked worktree (/Users/prajwal/.treehouse/video-editing-pilot-9487f5/58/
video-editing-pilot), not the installed engine - a broker started here would serve
production Resolve calls on unmerged code from a directory a worktree tool may
reclaim"
```

So a lane working in a **linked worktree** cannot place a live cut on this machine
at all: the broker is the machine's (and it is serving other lanes' queue), and the
client may not start one from a checkout a worktree tool may reclaim. The cut stage
belongs to a lane running from the installed engine checkout — which is how PR 3104
measured its 24 cuts.

So this lane did **not** place a cut, and it did **not** take the slot: taking it
would have moved another lane's Resolve session out from under it, which is exactly
what the slot exists to prevent.

**A defect this exposed, worth the next lane's attention.** The escape hatch in
`library/tools/resolve_scratch_projects.py` is "the holder is gone, so the next
writer takes that session back" — it compares the holder's **process**. The record's
owner is the **broker worker's** pid, not the caller's, because the broker executes
the scratch lifecycle in its own tree. The broker worker is alive for as long as the
broker is, so on a machine where a scratch session is created through the broker the
escape can never fire: the slot waits out its full timeout and then refuses, and the
only way it is released is the holder lane's own `restore`. Recording the **job's
requester** as the owner (the broker already carries it) would make the escape fire
when the lane that asked for the session is gone.

**What the cut's answer therefore rests on.** The chain's boundary is measured
(§Stage 4): the arrival rule names source frame **1377** for the occurrence the
model named (A3), and source frame **1002** for the ground truth's second arrival
(A2) — so the cut the M0 case asks for is the one at **boundary + 3**, on a linked
video+audio item, and the operation that places it is PR 3104's composed
picture-only cut, measured live on 24 cuts with the audio item's own state
unchanged. This lane did not add a twenty-fifth measurement to it; it says so
rather than borrowing another lane's.

---

## 5. The frozen M0 cases, corrected

The baseline's frozen cases (M0-0 … M0-8) are re-frozen against the **corrected**
scenario: the car keys, the whole clip, no pre-selected window. Today's column is
this lane's measurement.

| Case | Request | Pass condition | Today's measured result |
|---|---|---|---|
| **M0-0** corpus admissibility | "the second time the speaker brings the car keys to his lips" | ≥1 rights-cleared source carries ≥2 such arrivals, with an adjudicated onset frame for each | **partial** — one source (IMG_1822) carries **six** arrivals and the frames are adjudicated above, but the adjudication is **agent-adjudicated** (±2 frames on A2, ±7 on the rest); the captain's re-adjudication is still owed |
| **M0-1** event resolution | the arrival of the car keys at the lips | the canonical model carries an event for the arrival, with evidence ids and provenance | **pass (for the rise class)** — `object_pickup` carries 4 of the 6 arrivals as model events with occurrence blocks, evidence ids and provenance; **fail (for `hand_near_mouth`)** — one 64.78 s span, 74 of 89 coarse frames "near the mouth" |
| **M0-2** boundary precision | the boundary of that occurrence | \|boundary − truth\| ≤ **3 frames**, expressed as a source frame index | **pass with the `arrival` rule** — 0 frames on A2, 3 frames on the occurrence the model named; **fail with the rule the CLI offered** — 23 and 225 frames |
| **M0-3** repeat identity | "the second occurrence" | the model names the occurrence and it survives a re-measure | **pass for the axis** (22 events numbered, stable `asset\|class\|subject\|index` keys, `nth_event` answers with a receipt) · **fail for the count** — the coarse lane missed the first arrival, so the engine's second is the truth's third |
| **M0-4** grounded target | "cut 3 frames after that boundary" | a typed target resolves to one event id and one source frame, with no unresolved semantic left | **pass** — the chain resolves to one event id (`…-m7-0017`), one occurrence key and one source frame (1377 → the cut at 1380); the semantic that is left is the *choice* of class, which the ground truth does not share with the coarse lane |
| **M0-5** operation compilation | the same, as an operation | a production-enabled operation exists for a picture cut at an exact frame | **still not promoted** — the composed picture-only cut is measured (PR 3104) and is **not** production-enabled: the picture is replaced, so the audio's link does not survive |
| **M0-6** execution + read-back | place the cut on a scratch timeline | the cut lands on the exact frame, read back from Resolve, on a Ren-owned scratch project through the serving broker | **not_exercised** — the machine's scratch-origin slot was held by another lane's live run for the whole window (§4); the operation is measured by PR 3104 (24/24 live cuts, exact placement on both axes) |
| **M0-7** audio preservation | "without altering the audio" | the audio item's in/out, gain and link state are unchanged, proven by read-back | the audio item's id, record span, source span, gain, fades and enablement are unchanged; the **link** is not, and the read-back says so rather than claiming it |
| **M0-8** abstention honesty | "the person never brings the keys to his lips" | absent evidence ⇒ `not_yet_analysed`, never a false negative | **pass** — the identity lane's licence refusal is reported as *unavailable*, the object request answers `present` with its coverage map, and no lane claims absence it did not measure |

Scoring: each case is `pass` / `fail` / `partial` / `not_applicable` /
`not_exercised`, with the reason recorded; unknown measurements are absent, never
zero.

---

## 6. What is NOT proved here

* **The ground truth is agent-adjudicated.** A2 was adjudicated at 30 fps (±2
  frames); the other five arrivals were read off 2 fps sheets (±7 frames) and their
  frames are quoted as approximations. The captain's re-adjudication is still owed,
  exactly as it was for the baseline.
* **The contact distance is measured on ONE take by ONE speaker.** 0.25 face widths
  separates the contact from the approach on this fixture; it is not calibrated on
  the D-01 corpus, and it is declared (and overridable) rather than hidden.
* **The engine measures the HAND, the ground truth measures the KEYS.** The
  refinement's series is the hand-to-lips distance; the keys are held a few
  centimetres from the lips by the fingers around them. On A5 the hand never
  measured within 0.25 face widths of the lips inside the window at all, while the
  keys were visibly at them — the two quantities are not the same, and the report
  says which one each number came from.
* **The coarse lane's recall is the weak link, not the identity axis.** The
  occurrence axis, the receipts and the keys are sound; what is short is the coarse
  candidate's ability to see a lift that is already under way.
* **The `hand_near_mouth` candidate's threshold was not re-measured.** 1.5 face
  widths was measured on the four Craig angles; this take shows that it cannot
  separate the arrivals of a speaker who holds an object near his face. Re-measuring
  that threshold on a corpus that carries this case is the next lane's work, and it
  is a product decision (the threshold trades recall against the number of spans the
  verifier has to look at), not this lane's.
* **No cut of this lane's own.** The cut stage was not exercised here (§4): the
  machine's scratch-origin slot was held by another lane's live run, and the current
  Resolve project belonged to another live lane. The operation's own evidence is PR
  3104's — 24 live cuts, exact placement, the audio item unchanged, the link not
  preserved — and this lane's contribution to the case is the BOUNDARY the cut would
  follow (source frames 1002 and 1377) and the report's own ground truth.

---

## 7. Reproduction

```sh
cd <worktree>
export PIPELINE_PROJECTS_ROOT=/tmp/m0-e2e/projects

# the fixture: a byte copy of the whole take (the source is never written)
mkdir -p /tmp/m0-e2e/media
cp "<001/raw>/IMG_1822.MOV" /tmp/m0-e2e/media/IMG_1822_keys_take.MOV

# project + preflight
bin/ren new m0-keys --name "M0 Car Keys Whole Clip" --fps 30 \
     --footage /tmp/m0-e2e/media --no-speakers --shape picture-led
bin/ren edit m0-keys --only scan --only catalog --only temporal_index --auto --full-auto agent

# the memory lanes: M2 (89 frames, the source's own I-frame cadence)
bin/vep -m library.tools.source_memory frames /tmp/m0-e2e/projects/m0-keys
# M3 (89 frames, 87 faces, 81 hands)
bin/vep -m library.tools.person_measurements build /tmp/m0-e2e/projects/m0-keys \
     --memory-root ~/.local/share/vep/source_memory/projects/<project-scope>
# M7 with NO identity lane (the licence-blocked M3b is not needed)
REN_EVENT_LOG=/tmp/m0-e2e/projects/m0-keys/pipeline_output/logs/event_log.jsonl \
bin/vep -m library.tools.event_spans build /tmp/m0-e2e/projects/m0-keys

# the canonical model, numbered
bin/vep -m library.tools.project_model_evidence migrate /tmp/m0-e2e/projects/m0-keys \
     --commit --expected-revision 1 --reason "M0 whole-clip car keys e2e baseline"

# stage 1: the request-driven object detection over the WHOLE clip
bin/ren evidence m0-keys --object "car keys" --requirement presence \
     --acquire --max-cost-seconds 1200

# stage 3: the occurrence
bin/ren evidence m0-keys <plan.json>   # {"predicate":"nth_event","event_class":"object_pickup","nth":2}

# stage 4: the frame-exact boundary (the arrival rule shipped here)
bin/vep -m library.tools.frame_exact refine /tmp/m0-e2e/media/IMG_1822_keys_take.MOV \
     --frame-index 1015 --half-window 0.5 --rule arrival --out /tmp/m0-e2e/refine
```

Defect-naming tests: `tests/unit/tools/test_frame_exact.py` (the `arrival` rule, the
default rule's peak-versus-arrival defect, the truncation guard and the declared
contact distance). The incident: `docs/RULE_EVIDENCE.md#the-boundary-that-named-the-peak-of-a-hold`.
