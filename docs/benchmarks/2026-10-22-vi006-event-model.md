# VI-006 — the event model, and the questions about a RELATION between events

**Lane:** VI-006 of the Ren punchlist ("Expand the event representation") · **Base:** `20c3988f3`
(the tip of `main` when the lane started) · **Branch:** `fm/p-vi006-event-model` · **Date:** 2026-10-22

**What the B audit named as missing:** *"state_changes and participant_ids on events (0 % today) and
before/after/while queries"* — the workstream B audit's finding B11 and its gap G5. The audit
measured **0 of 182 events** on the largest real Ren-owned Project Model carrying a participant or a
state change, and no question about *before*, *after* or *while*.

**What this lane landed:** the typed event contract carries the participants and the state
transitions, and a question that names a relation is answered against the events' own intervals.

---

## 1. The measurement

### 1.1 The labelled corpus

`tests/fixtures/vi006_event_model.json` — one podcast clip in which **a laptop is opened twice**.
Every event carries the participants it names, the evidence that measured it, and (for the openings)
the lid's own transition. The corpus is labelled: each question names the anchor event it expects
and the set of related events it expects.

The bench: `python3 docs/benchmarks/vi006_event_model_bench.py` (it writes nothing; it reads the
fixture and the engine).

| | before (`20c3988f3`) | after |
|---|---|---|
| labelled questions | 4 | 4 |
| **resolved** | **1 / 4** | **4 / 4** |
| anchor resolved to the RIGHT event | 1 / 4 | 4 / 4 |
| related events named correctly | 1 / 4 | 4 / 4 |
| — of which the ordinal question ("the second time") | 1 / 1 | 1 / 1 |
| — of which the relational questions (before / after / while) | **0 / 3** | **3 / 3** |
| the negative ("she opens the lid" must not match the CLOSING of the same lid) | 1 / 1 | 1 / 1 |
| the lid's state chain | not measurable | `closed → opening → open → closed → opening → open` (5 transitions) |

**The denominators are the fixture's own:** 4 labelled questions, 1 negative, 5 transitions, 8 events.
**Before** is the base commit measured with the SAME fixture, the SAME labels and the same
script. The script is committed — `python3 docs/benchmarks/vi006_event_model_baseline.py
[/tmp/vi006_base]` scores any checkout — and the base is read with
`git archive 20c3988f3 | tar -x -C /tmp/vi006_base`. The base resolved the ordinal and nothing
else: a question naming *before*, *after* or *while* was answered with the events that merely
LOOKED like the ones asked for, because the surface had no relation to read.

**A second delta, measured on one probe:** an event that carries no action class at all, only the
measured transition of a lid from `closed` to `open`, is an OPENING. At the base commit
`search_events("she opens the laptop")` returned **0 / 1** such events; it returns **1 / 1** now.
That is the difference between answering on the event's evidence and answering on how the event was
worded, and it is what the unit test
`tests/unit/picture/test_temporal_relational_query.py::test_a_measured_state_change_answers_a_question_about_the_action`
names.

**After**, the three relational questions resolve to the events whose intervals say so:

| question | anchor | answer |
|---|---|---|
| what happened **before** the second time she opens the laptop | `…:005` (the second opening, 20.0 s) | the reach, the first opening, both speeches, the closing |
| what happened **after** the second time she opens the laptop | `…:005` | the speech at 22.0 s — **not** the speech that overlaps the opening |
| what was **while** the second time she opens the laptop | `…:005` | the speech at 20.2 s, which shares the opening's time |

### 1.2 The same surface on real footage

`python3 docs/benchmarks/temporal_event_reach_bench.py` — the typed index over the Ren scratch
projects (read only; the index is derived, so the measurement writes nothing).

| project | events | state changes | participants | `participant_ids` | observed / inferred | state chains |
|---|---|---|---|---|---|---|
| `ren-scratch-breaktest` | 9 659 | 763 (7.9 %) | 7 932 (82.1 %) | **7 932 (82.1 %)** | 7 932 / 0 | 18 |
| `ren-scratch-bench-demo` | 530 | 512 (96.6 %) | 512 (96.6 %) | **512 (96.6 %)** | 512 / 0 | 11 |
| `ren-scratch-bench-demo2` | 530 | 512 (96.6 %) | 512 (96.6 %) | **512 (96.6 %)** | 512 / 0 | 11 |
| `ren-scratch-retrieval-bench` | 530 | 512 (96.6 %) | 512 (96.6 %) | **512 (96.6 %)** | 512 / 0 | 11 |
| `vep-live-workflows-e2e` | 0 | — | — | — | — | 0 |
| `eval04-scratch-renders` | 0 | — | — | — | — | 0 |

11 249 typed events across 4 of 6 projects. The event counts, the state-change counts and the
participant counts are **unchanged** by this lane (the same measurement at the base commit reads
9 659 / 763 / 7 932 and 530 / 512 / 512): nothing was re-derived, so there is no fidelity
regression to revert. What changed is that the participants are now carried under the event
contract's own `participant_ids` field, with the observed and the inferred kept apart, and that a
property which changed more than once is now readable as a chain.

**A note on the observed / inferred split:** it reads 7 932 / 0 because the events that carry
participants on real footage are the MEASURED ones (a tracked region, a measured voice turn). No
event on these projects carries a participant established only by an inference, so the inferred side
of the split is **unexercised by real data**, not absent by construction.

---

## 2. What the lane changed

### 2.1 The event contract (`library/tools/temporal_events.py`)

* **`participant_ids`** — every entity an event names, persons and objects alike, under the Project
  Model's own field name. `observed_participant_ids` and `inferred_participant_ids` keep the two
  apart: a participant the picture showed is an observation; a participant an inference put there is
  not.
* **`location`** — the place an event names, read from the scene observation and the measured scene
  slot.
* **`unknown_attributes`** — a PARTIAL event is kept and says which of its attributes nobody
  determined. An absent attribute that reads as "there was none" is a claim nobody measured.
* **`StateChange.certainty`** — a transition says how it was established, and refuses a certainty
  outside the vocabulary.
* **`state_transitions` / `state_history` / `state_at`** — the chain a property makes
  (`closed → opening → open`), the values it took, and the value it held at a moment. A property
  nobody measured returns *unknown*, never "no value".

### 2.2 The question surface (`library/tools/temporal_query.py`)

* **A relational reference** — "before", "after", "while" (and "during") name a relation and an
  anchor. The anchor is resolved to ONE event by the ordinal machinery; the events in the relation
  are the ones whose intervals on the SAME clock say so. Every row carries `certainty: measured` and
  the measurement it rests on.
* **An anchor that matches several events is REFUSED** — "what happened before she opens the laptop"
  with two openings and no ordinal is ambiguous, and the answer says so rather than picking one.
* **The anchor is resolved on EVENT EVIDENCE** — an event whose measured transition reached `open` is
  an opening, whatever a lane worded it as; and the DIRECTION is what counts, so a lid that reached
  `closed` is a closing and never matches a question about opening. The negative in the corpus is
  exactly that case.
* **`answer()`** carries the relational resolution, and for a relational question its `events` are
  the events in the relation (the anchor is named in the report).

### 2.3 The reachable surface

`ren search <project> --temporal "what happened before the second time she opens the laptop"` reaches
all of it, and the printed answer now shows the relation, its anchor and the measured rows.
`.agents/skills/ren-co-editor/SKILL.md` says so.

---

## 3. What was left undone

* **The canonical Project Model's own event rows still carry empty `participant_ids` and no
  `state_changes`** — measured again after this lane: 182 events, 0 with a participant, 0 with a
  state change. Those rows are `cinematography_*` events: a cut and a segment, written by the VI-09
  migration. Filling them means deciding what a CUT's participants are, and the answer the product
  reads is already there: the typed index names who was in the picture from the tracks and the
  observations that measured it. A migration that writes participants into the model rows is a
  separate change to the model's content, and it is not made here.
* **The action events the model would need for a real `closed → opening → open` are still the
  product's job.** The fixture's transition is measured on a corpus we control; the ladder that
  measures a lid opening on real footage (a detector for the lid, a tracker for it, a state reader
  over the two) does not exist yet. This lane gives that ladder somewhere to land — a transition is
  a first-class part of an event, with its evidence and its certainty — and does not build it.
* **No live Resolve run.** Nothing in this lane touches Resolve, so no broker was started, no lease
  was asked for, and no captain project, timeline or media was read or written. The real-footage
  measurement reads the Ren scratch projects' own files.
* **The relational questions resolve on ONE clip's clock.** An event on another clock is never
  related to the anchor, so "before the second time she opens the laptop" cannot reach across a cut
  to another shot. The refusal is deliberate and reported; widening it needs a cross-source
  ordering the footage does not yet carry.
