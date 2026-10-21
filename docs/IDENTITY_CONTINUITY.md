# Identity continuity: one person, one person

**What this is.** The rule set behind `library/tools/person_entity.py`'s
roster and `library/tools/identity_continuity.py`'s measurement of it.

**The problem it answers.** A two-camera conversation measures one person
twice. The camera on a speaker picks up the OTHER speaker's voice
whenever the other speaker is talking, and the diarizer files it as a
second voice track of that camera. The person on screen is therefore not
always the person talking, and a roster that reads "whoever is on screen
is talking" gives one speaker's speech to the other person - on one
camera, and not on the other. A reference to that speaker then resolves
to two different people depending on the angle.

## The rules

**A voice belongs to the person its MEASURED voice says it is.**
The cross-source ECAPA components (`person_entity.voice_components`) are
the measurement: a component is one speaker's voice across cameras,
takes and cuts. Its owner is the person holding a **majority of the
component's speech duration** among the members whose co-occurrence names
exactly one person. A member whose co-occurrence named somebody else is
re-attributed to the owner, and the contradiction is filed in
`voice_reassignments` - the person on screen is not always the one
talking, and the measured voice outranks the co-occurrence.

**A component with no majority is a doubt, not a merge.** Two people each
claiming a voice the measurement cannot separate: nothing in that
component is joined and nothing in it is moved. It is returned in
`voice_match_conflicts`.

**A voice the measurement CONTRADICTS is never force-attached.** Two voice
tracks of one source that the co-occurrence gave to the same person, and
whose ECAPA similarity is below `VOICE_MATCH_THRESHOLD`, cannot both be
that person's voice. The one that matches that person's other voices
keeps the person; the other is left unresolved and filed in
`voice_unresolved`, with the measurement that refuted it.

**Faces are grouped by complete linkage, and an undecidable face is left
alone.** A track joins a person only when it matches EVERY track already
in that person, and only when it beats the runner-up candidate by
`AMBIGUITY_MARGIN`. The running centroid this replaced drifts as members
join it, so one person's changed appearance falls out of its own group
and a second person can be pulled into it. A placement the measurement
cannot decide starts its own person and is reported in
`uncertain_persons` - never a forced merge.

**The roster is project-local.** It is built from this project's own
sources, and nothing in it links to another project. A source the
project has no identity measurement for is reported in
`sources_without_identity_measurement`, not dropped.

## The measurement

`library/tools/identity_continuity.py` judges a roster against a labelled
corpus. The manifest labels who is ON SCREEN per source (a fact about
the footage) and who is SPEAKING per diarized voice track:

```json
{"sources": [{"content_digest": "<64 hex>", "person": "Craig",
               "voices": {"voice_001": "Craig", "voice_002": "uncertain"}}]}
```

`uncertain` marks a track whose speaker the measurement does not
establish - a fragment that matches nobody above the threshold.

```
python3 -m library.tools.identity_continuity study <project> --manifest <manifest.json>
```

The two-camera podcast corpus is labelled in
`docs/IDENTITY_CONTINUITY_PODCAST_MANIFEST.json` (the media itself is the
captain's own and is read in place; the manifest names no path to it).

## Measured on the two-camera podcast

A Ren-owned scratch copy of the seven geo-podcast sources (three on
Akshita's camera, four on Craig's; 82 and 68 minutes of the long ones).
The source media was read, never written. The corpus's M3b identity
measurements were already in the machine's source-memory store, so no
model was re-run for this measurement.

**The labels.** Each camera's own person is the ground truth for who is
on screen (the same file-level labelling the person-entity-store FAR/FRR
study used: `LC49xx` is Akshita's camera, `LCATL00xx` is Craig's). Each
voice track's speaker is the camera's dominant voice where the camera
holds one speaker, and otherwise the speaker the track matches across
angles at 0.86-0.97 ECAPA cosine - above the measured 0.45 threshold.
The two fragments that match no speaker above the threshold
(`LCATL0012/voice_002` at 0.31 and `LCATL0013/voice_002` at 0.16 to
their own camera's dominant voice) are labelled `uncertain`. The labels
therefore rest on the same measurement the engine uses; what is being
judged is whether the engine's OWN conclusion (the co-occurrence, the
component owner) agrees with them.

### Before and after

| | before | after |
|---|---|---|
| Craig's speech on his OWN person | 4/7 tracks, 848.5/1088.5 s (78.0%) | **7/7 tracks, 1088.5/1088.5 s (100%)** |
| Craig's identity split across people | **yes** (person_001 and person_002) | no |
| Akshita's speech on his own person | 3/3 tracks, 1989.0/1989.0 s | 3/3 tracks, 1989.0/1989.0 s |
| roster persons holding TWO speakers | **1 of 2** (person_002: Akshita + Craig, 2229.0 s) | **0 of 2** |
| uncertain tracks force-attached anyway | **1 of 2** | **0 of 2** |
| uncertain tracks left unresolved | 1 of 2 | **2 of 2** |
| roster persons | 2 | 2 |
| labelled sources | 7 | 7 |
| labelled voice tracks | 12 | 12 |

Denominators are the rows above: 12 labelled voice tracks over 7 sources
and 2 roster persons. Unmeasured is never zero - every count states what
it is a count OF.

**The three voice tracks that moved** are the ones the defect is about:
`LC4930/voice_001` (56.5 s), `LC4931/voice_001` (32.0 s) and
`LC4932/voice_002` (152.0 s) are Craig's voice, picked up by Akshita's
cameras. Before, they were in Akshita's speech history; after, they are
in Craig's, on both angles.

**What did not change.** The face side of the roster: both cameras'
faces still resolve to one person per speaker, and the measured face
separation on this corpus (0.96+ same-person against 0.03-0.05
different-person) is far outside the ambiguity margin, so no face became
uncertain. The completeness-linkage change and the margin gate are
defences against a corpus the face study could not measure (it had two
people); this corpus does not exercise them.

**The limits, stated.** Two people exist in this corpus, so "another
person with similar appearance" is not measured - it is the region the
face study named as unmeasured. The voice labels rest on the same ECAPA
measurement the engine uses. The voice tracks the diarizer files as
fragments carry under 21 s and take no part in the cross-source
matching, so they are decided (or left alone) inside one source only.

## The defect it fixes

`docs/RULE_EVIDENCE.md` is the history behind `AGENTS.md`; this file is
the history behind the roster. The defect in one line: **the person on
screen was taken to be the person talking, so one speaker's speech
resolved to the other person on one camera and to himself on the other.**
