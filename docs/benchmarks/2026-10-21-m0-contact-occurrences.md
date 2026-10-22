# M0 whole clip: the repeated arrivals, split into occurrences

**Lane:** `g-m0-l7-whole-clip-occurrences` · **Date:** 2026-10-21 ·
**Fixture:** the whole 85.82 s take of Apple 001 `IMG_1822`, on a scratch
byte copy · **Ground truth:** the one written down in
[`2026-10-21-m0-e2e-whole-clip.md`](2026-10-21-m0-e2e-whole-clip.md), unchanged
(agent-adjudicated, still provisional).

**What the lane was asked to do.** The M0 whole-clip lane left one thing
unanswered and called it a product decision: the coarse candidate stage
measured "the hand is NEAR the mouth" with ONE fixed distance (1.5 face
widths), and on a take whose speaker holds the car keys near his face
throughout, that answer is true on 74 of the 89 coarse frames - so the six
by-eye arrivals of the keys at his lips collapsed into **ONE 64.78 s span**
and the chain could not name the second one. Re-measuring that threshold was
called a product decision. It is not: the captain's ask is that Ren *isolate
the occurrences from the whole clip*.

**Short answer.** The coarse stage now measures the arrivals **per take** -
the hand REACHED the lips, and was RELEASED before the next time, with the
release level measured on the take itself - and the whole take yields **12
separate contact-onset occurrences** where it used to yield one merged span.
Six of the twelve carry an adjudicated arrival; the second adjudicated
arrival (33.40 s, frame 1002) is now an occurrence of its own, and the
refinement of that occurrence names its arrival frame at **0 frames** error
with the chain's own window (and 10 frames early with the wider one, because
the engine measures the HAND and the truth measures the KEYS - §6).

**What the chain can now say, and what it still cannot.** The chain names the
second *occurrence* of the class: with 12 measured occurrences against 6
adjudicated arrivals, "the second time" is the engine's second contact
episode (15.47 s), which is the adjudicated **first** arrival. The second
ADJUDICATED arrival is the engine's fifth occurrence. Before this change the
chain named a 68.167-70.101 s moment for "the second time" and that moment
measured **no arrival at all**.

---

## 1. The fixture, the ground truth, and what was NOT re-measured

| | |
|---|---|
| Source (READ ONLY, never written) | `<001/raw>/IMG_1822.MOV` - Apple 001 `IMG_1822`, 85.82 s, 2574 frames @ 30 fps, 1920x1080 |
| Scratch copy (byte copy) | `/tmp/m0-e2e/media/IMG_1822_keys_take.MOV`, `md5 f3af6808c314cadefc15218baf273037` - identical to the source |
| Project (scratch) | `/tmp/m0-e2e/projects/m0-keys`, `PIPELINE_PROJECTS_ROOT=/tmp/m0-e2e/projects` |
| M2 (the coarse sample) | 89 frames on the source's own I-frame cadence, median spacing **0.967 s** - UNCHANGED, not re-measured |
| M3 (faces and hands) | 89 frames, 87 faces - UNCHANGED, not re-measured |
| The change under test | the coarse candidate stage (`library/tools/event_spans.py`) and the refinement's contact level (`library/tools/frame_exact.py`) |

The picture was not re-measured: the lane's question is what the coarse stage
DOES with the measurements it already has.

**The ground truth, unchanged and still agent-adjudicated.** The six arrivals
of the car keys at the speaker's lips, written down in the earlier lane's
report: **A1 15.4 s (frame ~462)**, **A2 33.40 s (frame 1002, adjudicated at
30 fps, +-2 frames)**, **A3 46.0 s (~1380)**, **A4 53.0 s (~1590)**, **A5 61.5 s
(~1845)**, **A6 82.5 s (~2475)**. The adjudicator's own rule was "the
occurrences separated by less than a second of the keys being away are one
occurrence". The captain's re-adjudication is still owed, exactly as it was
for the baseline.

Nothing in the captain's projects, timelines or media was touched; no broker
was started; `ren qualification reset` was not run. Every measurement below is
of the scratch copy.

---

## 2. BEFORE: the coarse stage as it stood

The preserved artifacts of the pre-change run are in `/tmp/m0-l7/before/`
(the M7 record `events.json` and the canonical model `snapshot.json`).

**The class carried six spans, and the first one held every arrival.**

| # | span (s) | tier | what it is |
|---|---|---|---|
| 1 | **3.385 - 68.167 (64.78 s)** | 0 | every arrival of the keys, plus every moment the hand was near the face |
| 2 | 68.167 - 70.101 | 1 | a fragment after the first span ended |
| 3 | 70.100 - 73.969 | 0 | a fragment |
| 4 | 75.901 - 76.869 | 0 | a fragment |
| 5 | 76.869 - 77.835 | 1 | a fragment |
| 6 | 80.737 - 84.603 | 0 | a fragment |

Six spans is not six arrivals: five of them are fragments of the same
"near the mouth" hold, and the first is a 64.78 s span that contains all six
adjudicated arrivals.

**The chain's answer to "the second time"** (`nth_event(hand_near_mouth, 2)`,
run against the pre-change model, receipt
`asset-47909f5c…|hand_near_mouth|-|2`):

```
interval 68.167 s - 70.101 s        (the class's SECOND occurrence)
```

**Its refinement at 30 fps** (`frame_exact refine --frame-index 2059
--half-window 0.5 --rule arrival` over the coarse frame the span starts on):

| rule | result |
|---|---|
| `arrival` (the first frame at which the hand is AT the lips) | **`no_boundary_measured`** - not one frame in 68.149-69.149 s is within 0.25 face widths of the lips |
| `closest_approach` (the rule the command line offered) | frame **2065** (68.850 s) - **1063 frames after** the adjudicated frame 1002 |

**And the second adjudicated arrival was not nameable at all.** 33.40 s sits
inside occurrence #1, and no occurrence of the class carries it: the nearest
occurrence boundary is the span's own start at 3.385 s, thirty seconds away.
The chain could not point at it, let alone name its frame.

**The refinement was never the defect.** Asked directly about the truth's
second arrival - refining its coarse frame 1015 (33.842 s) with the `arrival`
rule - the engine names **frame 1002 (33.408 s)**, the adjudicated arrival
frame, **0 frames** error. The frame-exact stage could name the arrival; the
coarse stage could not isolate it.

---

## 3. AFTER: what the coarse stage measures now

`library/tools/contact_onsets.py` is the new enumeration, and it is where the
two levels live:

* the **contact level** - the distance at which the hand is AT the lips -
  is the DECLARED `CONTACT_DISTANCE_FACE_WIDTHS = 0.25` face widths, the
  number the refinement's `arrival` rule already used, read from ONE place
  (`frame_exact` now imports it, so the coarse stage and the refinement
  cannot drift apart);
* the **release level** - the distance at which the hand has LEFT the lips -
  is **MEASURED ON THE TAKE**: the take's own best two-way split of its
  measured distances (Otsu), taken as the midpoint between the two values
  the split falls between. A speaker who never goes far from his face
  measures a low release level; one who holds the object near his face
  measures a high one.

An **occurrence** is a maximal stretch of the take during which the hand was
never released, and it counts only when the hand reached contact inside it.
Each occurrence carries the coarse frame the arrival was SEEN at
(`onset_index`), the frame the hand was deepest at (`closest_index` - a
different moment, and reported beside the onset so a reader cannot confuse
the two), the contact frames, and both levels it was measured at.

The new candidate class is **`hand_contact_onset`**, and it is ADDITIVE:
`hand_near_mouth` keeps its meaning ("the hand is near the mouth", the
loose candidate the VLM verifies), because that class is also the query
surface `ren search --predicate hand_near_mouth` answers, and changing its
span set would change a product answer rather than fix a measurement. The
new class is the MEASURED occurrence axis.

**The measured result on the whole take:**

| | before | after |
|---|---|---|
| spans of the class | 6 (one of them 64.78 s) | **12 contact-onset occurrences** |
| the class's own release level | - | **0.646 face widths** (the take's own split of its 75 measured distances) |
| occurrences that carry an adjudicated arrival | 0 | **6** |
| the second adjudicated arrival (33.40 s) | inside a 64.78 s span, not nameable | **occurrence #5**, 33.358-38.191 s |
| the chain's "second time" | 68.167-70.101 s, **no arrival measurable** | 14.987-17.886 s - the adjudicated **first** arrival |

The M7 record carries the count in its own account
(`"hand_contact_onset_candidates": 12`), and the canonical model carries the
twelve as numbered events (`{"class": "hand_contact_onset", "index": 1..12}`),
so the occurrence identity numbers them without a new mechanism.

---

## 4. The measurement: occurrences found vs the six adjudicated

Every occurrence, refined at 30 fps on the scratch copy with
`frame_exact refine --rule arrival`, against the adjudicated truth. **Two
windows are reported**, because the window is a declared parameter of the
refinement and the coarse onset can be up to one coarse cadence (0.967 s)
late:

* **±0.5 s** - the window the M0 chain used on this fixture, so the
  before/after comparison is like for like;
* **±1.0 s** - the take's own coarse cadence, the window that cannot cut the
  arrival off (it contains the hand's first contact even when the coarse
  sample missed it).

| # | coarse onset (s) | coarse frame | ±0.5 s frame | error vs truth | ±1.0 s frame | error | adjudicated |
|---|---|---|---|---|---|---|---|
| 1 | 3.868 | 116 | 104 | -358 | 104 | -358 | - |
| 2 | 15.470 | 463 | **461** | **-1** | 432 (truncated) | -30 | **A1** (~462) |
| 3 | 21.272 | 638 | 631 | +169 | 631 | +169 | - |
| 4 | 25.140 | 754 | 738 | -260 | 738 | -260 | - |
| 5 | **33.842** | **1015** | **1002** | **0** | 992 | -10 | **A2** (1002) |
| 6 | 46.412 | 1392 | 1377 (truncated) | -3 | **1377** | **-3** | **A3** (~1380) |
| 7 | 50.278 | 1507 | 1492 (truncated) | -98 | 1491 | -99 | - |
| 8 | 54.147 | 1624 | 1609 (truncated) | +19 | **1597** | **+7** | **A4** (~1590) |
| 9 | 63.815 | 1914 | 1907 | +62 | 1907 | +62 | **A5** (~1845) |
| 10 | 70.583 | 2117 | 2104 | +259 | 2104 | +259 | - |
| 11 | 76.385 | 2291 | 2287 | -188 | 2287 | -188 | - |
| 12 | 83.153 | 2494 | 2479 (truncated) | +4 | **2474** | **-1** | **A6** (~2475) |

**Occurrences found: 12. Adjudicated arrivals carried: 6** (A1 by #2, A2 by
#5, A3 by #6, A4 by #8, A5 by #9, A6 by #12). Every adjudicated arrival is
inside a measured occurrence; five of the six are named within 19 frames of
the adjudicated frame and one (A5) is 62 frames late, because the coarse
sample that caught that arrival measured 0.362 face widths - just outside the
contact level - and the next sample it caught was 2.3 s later.

**The second adjudicated arrival: named, and its frame error.**

| | before | after |
|---|---|---|
| can the chain point at the second adjudicated arrival (33.40 s)? | **no** - it is inside occurrence #1, a 64.78 s span, and no occurrence carries it | **yes** - occurrence #5 (33.358-38.191 s), receipt `asset-47909f5c…|hand_contact_onset|-|5` |
| its refined arrival frame | - | **frame 1002** (±0.5 s window) = **0 frames** error; frame 992 with the ±1.0 s window = 10 frames early (§6) |
| what the chain names for "the SECOND time" | 68.167-70.101 s, and **no arrival is measurable in it** | 14.987-17.886 s, the adjudicated **first** arrival, refined to frame 461 (**-1 frame**) |

---

## 5. The six extras, looked at

The class found six contact episodes the adjudicated six does not list. They
were looked at, frame by frame, on the scratch copy:

| # | onset (s) | what the picture shows | what it is |
|---|---|---|---|
| 1 | 3.868 | the fingers **and the fob at his lips** | a real keys-at-lips moment the adjudicated six does not list |
| 3 | 21.272 | the **keys at his lips** | a real keys-at-lips moment the adjudicated six does not list |
| 4 | 25.140 | the fingers at his lips, the keys a centimetre below | a hand-at-lips contact, not a keys arrival |
| 7 | 50.278 | the fingers at his lips, the keys hidden below | a hand-at-lips contact, not a keys arrival |
| 10 | 70.583 | the hand at his brow, not at his mouth | a coarse-geometry artifact (the 384x216 sample) |
| 11 | 76.385 | no hand at the mouth at all | a coarse-geometry artifact |

So the class's ordinal and the truth's ordinal are not the same axis: the
engine measures the HAND (12 contacts) and the truth measures the KEYS (6
arrivals), and at least two of the twelve are keys-at-lips moments the
adjudication omitted. The adjudicated six is agent-adjudicated and
provisional, and this lane's measurement says it is short at least two
arrivals - which is the captain's re-adjudication to settle, not this lane's.

---

## 6. What is NOT proved here

* **The engine measures the HAND; the truth measures the KEYS.** Two
  quantities that are close and not the same. On A2 the hand is at the lips
  from frame 992 (33.075 s) while the keys touch the lips at frame 1002
  (33.408 s), so the arrival the engine names is up to 10 frames before the
  adjudicated one - and the ±0.5 s window reports 0 frames because it starts
  after the hand's own first contact. Both numbers are in the table; neither
  is the other.
* **The ground truth is agent-adjudicated** and still provisional: A2 at 30
  fps (±2 frames), the other five off 2 fps sheets (±7 frames), and §5 says
  the six are short at least two arrivals. The captain's re-adjudication is
  owed.
* **The release level is measured on ONE take.** 0.646 face widths is this
  take's own split; a take whose speaker behaves differently measures
  differently, and that is the point of measuring it per take - but no
  corpus has measured it yet.
* **The refinement window is a declared parameter** and it moves the answer:
  ±0.5 s and ±1.0 s differ by up to 30 frames on A1 and 10 on A2, and the
  ±0.5 s window reports `boundary_truncated` on four of the twelve. The
  truncation flag is the engine saying the window may have cut the contact
  off, which is why the wider window is reported beside it.
* **No cut was placed.** The cut stage belongs to a lane running from the
  installed engine checkout (the broker is the machine's, and a linked
  worktree may not start one); the operation's own evidence is PR 3104's 24
  live cuts.
* **The ordinal is still not the truth's ordinal** (§5). The class names the
  second CONTACT episode; the truth names the second KEYS arrival. Naming the
  second arrival of a specific object needs an object measurement the coarse
  stage does not have.

---

## 7. Reproduction

```sh
cd <worktree>
export PIPELINE_PROJECTS_ROOT=/tmp/m0-e2e/projects

# the fixture: a byte copy of the whole take (the source is never written)
mkdir -p /tmp/m0-e2e/media
cp "<001/raw>/IMG_1822.MOV" /tmp/m0-e2e/media/IMG_1822_keys_take.MOV

# project + preflight (unchanged from the earlier lane; the picture is NOT
# re-measured here)
bin/ren new m0-keys --name "M0 Car Keys Whole Clip" --fps 30 \
     --footage /tmp/m0-e2e/media --no-speakers --shape picture-led
bin/ren edit m0-keys --only scan --only catalog --only temporal_index --auto --full-auto agent

# the memory lanes (M2 89 frames, M3 87 faces) and the coarse stage
bin/vep -m library.tools.source_memory frames /tmp/m0-e2e/projects/m0-keys
bin/vep -m library.tools.person_measurements build /tmp/m0-e2e/projects/m0-keys \
     --memory-root ~/.local/share/vep/source_memory/projects/<project-scope>
bin/vep -m library.tools.event_spans build /tmp/m0-e2e/projects/m0-keys
#   -> "hand_contact_onset_candidates": 12

# the canonical model, numbered
bin/vep -m library.tools.project_model_evidence migrate /tmp/m0-e2e/projects/m0-keys \
     --commit --expected-revision 5 --reason "M0 whole-clip: contact onsets"

# the occurrence
bin/ren evidence m0-keys <plan.json>   # {"predicate":"nth_event","event_class":"hand_contact_onset","nth":5}

# the frame-exact arrival
bin/vep -m library.tools.frame_exact refine /tmp/m0-e2e/media/IMG_1822_keys_take.MOV \
     --frame-index 1015 --half-window 0.5 --rule arrival --out /tmp/m0-l7/refine
```

Defect-naming tests: `tests/unit/picture/test_contact_onsets.py` (the merged
span, the per-take release level, the onset against the closest approach, the
held take, and the occurrence axis itself). The incident:
`docs/RULE_EVIDENCE.md#the-arrivals-that-were-one-span`.
