# The query interface: media intervals, grounded and honest

Owner: `library/tools/world_model_query.py`. This is the CONTRACT a
consumer reads, not a tutorial: an animation tool, a sound designer or
an asset library asks the world for media and gets intervals it can act
on without knowing anything about timelines, reels or Resolve.

    python3 -m library.tools.world_model_query search <project> \
        --clip clip_003 --mode action --action hand_near_mouth
    python3 -m library.tools.world_model_query search <project> \
        --clip clip_003 --mode order --of action \
        --action hand_near_mouth --ordinal second
    python3 -m library.tools.world_model_query evidence <project> \
        --clip clip_003 --start 40 --end 55

## The modes

One enumeration, `world_model_query.SEARCH_MODES`:

| Mode | The query names | The lanes it reads |
|---|---|---|
| `speech` | the words (`text`), optionally an `entity` | m1 (the transcript), m7 (the measured turns) |
| `appearance` | an `entity` and/or an `expression` | m7 (who is on screen), m3b (the detected face), m3c (the expression) |
| `action` | an `action` predicate and/or an `entity` | m7 (the candidates), m8 (the verdicts) |
| `identity` | an `entity`: a face track, a voice track or a declared name | m3b, m7, m10 |
| `relation` | a `relation` predicate and/or an `entity` | m10 |
| `order` | `of` (the mode it orders), an `ordinal` and/or a `relative` with a `reference` | the mode it orders |

A search that names a lane which was never built answers with no
intervals and NAMES the missing lane: `lanes_consulted_absent` lists it
and `unanswered` says why. An absent lane is never an empty answer, and
"nothing happened here" is never a thing this interface says about a
lane it never read.

## The interval

Every interval is the same shape, whatever mode produced it:

```json
{
  "aspect": "events",
  "kind": "hand_near_mouth",
  "entity": "face_001",
  "start": 2.336,
  "end": 5.839,
  "value": {"face_track": "face_001", "person": null, "verdicts": []},
  "provenance": {
    "lane": "m7", "slot": "events.json",
    "reader": "event_spans.read_m7",
    "field": "candidates.hand_near_mouth.spans",
    "status": "candidate",
    "class": "inferred", "claim": "candidate_span",
    "producer": "library.tools.event_spans",
    "measurement_type": "event.candidate_span"
  },
  "source_reference": {
    "kind": "interval",
    "stream_id": "stream-<digest>",
    "interval": {"clock_id": "clock-<digest>", "start": 2336, "end": 5839}
  },
  "confidence": {"value": null, "basis": null},
  "uncertainty": {"class": "inferred", "status": "candidate",
                  "reason": "a geometric candidate, not an answer: ..."},
  "occurrence": 2
}
```

- **`start` / `end`** are the SOURCE's own seconds, at the canonical
  record's own timing — never the edge of a second and never a
  re-derived boundary.
- **`value`** carries the record's own payload. The keys are the
  record's, not this product's.
- **`provenance`** names the lane, the slot, the READER that produced
  the fact and the field it came from; `status` says whether the lane
  measured it (`measured`), only CANDIDATED it (`candidate`), and
  whether a verifier judged it (`verified`, `rejected`).
- **`provenance.class`** is the epistemic class the lane's OWN producer
  earned (`claim_provenance`): `measured`, `inferred` or `interpreted`.
  It is never chosen by the reader.
- **`source_reference`** is the universal source reference
  (`library/tools/source_reference.py`): an interval on the source's
  own millisecond clock. It names the SOURCE, never a derived record,
  and it is the same spelling the canonical Project Model uses.
- **`confidence`** is a measured score and the field it came from, or
  `null` with `basis: null`. A placeholder is not a score: the
  transcript lane's `confidence: 0.0` is reported as no score at all.
- **`uncertainty`** says how far the claim can be trusted: its class,
  its status, and the named reason there is no confidence.
- **`occurrence`** numbers the row's occurrence OF ITS KIND, so "the
  second time" is a nameable answer.

## The grounding block

Every answer carries the grounding its intervals are placed in:

```json
{"content_digest": "<sha256>", "asset_id": "asset-<digest>",
 "stream_id": "stream-<digest>", "clock_id": "clock-<digest>",
 "ticks_per_second": 1000, "tick_unit": "millisecond"}
```

A consumer that holds this can place any interval inside the original
media.

## The evidence

`World.evidence(start, end)` answers "what is behind this span" with
the records, the frames and the verdicts:

- `intervals` — the span's own facts, with the grounding above;
- `evidence` — the frames sampled inside the span (each with its path
  on disk), the verdicts on the candidate spans, the lane record each
  fact was read from, and an `absence` row when a lane was never built;
- `resolution` — where the bytes are, when the canonical Project Model
  can place the reference; otherwise the NAMED refusal
  (`missing`, `stale`, `ambiguous`, `incompatible`).

Nothing in the evidence path needs a running Resolve, so it can be
inspected on a machine that has none.

## The rules this interface keeps

1. **A question costs the question.** The lanes are read lazily, one
   mode at a time: a relation search never pays for the transcript.
2. **A lane that was never built is ABSENT and is named.** It is never
   defaulted, and never answered as "nothing happened".
3. **A candidate is never an answer.** A geometric candidate says so,
   and the verdict that judged it rides with it.
4. **The class belongs to the producer.** A fact is filed under the
   class the lane's own declaration earns.
5. **A score is a score.** A lane that stored no number gets a named
   uncertainty, not a zero.
6. **No reference, no claim.** A measurement that cannot be counted on
   the source's clock carries no reference and says why.
7. **One vocabulary.** The interval, entity and ask commands answer
   with the same grounding as the search, so a consumer that learned
   one has learned all of them.
