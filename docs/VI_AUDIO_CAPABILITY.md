# Speech and audio intelligence, item by item

The captain's *Ren - Detailed Capability Improvement Punch List* (baseline
2026-10-20), section **4.4 "Strengthen audio and speech intelligence"**:
VI-028 through VI-035. This is the account of what each item needed,
what was already in the repository, what this lane added, and what is
still unmeasured.

Two lanes, one brief: `fm/cap-vi-audio` (PR 2902, merged as 1ddc1312a)
and `fm/cap-vi-audio-2` (PR 2944).

| Item | State | Where it lives |
|---|---|---|
| VI-028 speech word/timestamp alignment | **extended** | `library/tools/alignment_quality.py`, `hybrid_transcription.py`, `capabilities/temporal/index.py`, `source_memory.py` |
| VI-029 speaker separation during overlap | **extended** | `library/tools/overlapped_speech.py`, `library/tools/diarization_eval.py`, `library/tools/speech_measurements.py` |
| VI-030 cross-segment continuity, speaker-to-camera | **already done** | `library/tools/person_entity.py`, `voice_identity_study.py`, `asd_lr.py`, `cross_modal_grounding.py`, `evidence_to_edit.py` |
| VI-031 retakes, repeated lines, preferred takes | **already done** | `library/tools/retake_scan.py`, `speech_take.py`, `take_selection.py`, `take_pick_preview.py` |
| VI-032 edit-safe versus important pauses | **new** | `library/tools/pause_class.py`, `project_model_evidence.py` |
| VI-033 relevant non-speech events | **new** | `library/tools/sound_class.py`, `library/tools/audio_interval_evidence.py` |
| VI-034 event localization, absent versus uncertain | **new** | `library/tools/sound_class.py`, `project_model_evidence.py`, `context_views.py` |
| VI-035 preserve the audio reference and alignment | **already done** | `library/tools/audio_preserving_replacement.py`, `dialogue_cleanup.py`, `edit_correction.py`, `source_clocks.py`, `spine_contract.py` |

Nothing here was built on a parallel path. Each item lands beside the
producer that already owns the measurement.

---

## VI-028 - Improve speech word/timestamp alignment on noisy and conversational recordings

**Already done.** Transcription is Voz's text through MFA's forced
alignment (`library/tools/hybrid_transcription.py`), with the window
dials settled by a 21-configuration sweep (`SILENCE_SPLIT_SECONDS`,
`WINDOW_PAD_SECONDS`), a refusal for a language the aligner cannot
cover, a refusal for a window that aligned nothing, and a tolerance
for a few unaligned windows (`mfa_align.MAX_UNALIGNED_FRACTION`).
A window no pass can align keeps the transcriber's own timings, marked
`timing_source: transcriber`, and the hybrid's record counts those
words. The aligner's own per-word score is gone with MFA, and
`transcript_confidence.ALIGNMENT_SCORE_ABSENT_MFA` says so instead of
publishing a stand-in.

**What was missing.** The production path published no alignment
QUALITY at all. The quality of an alignment was measured offline by
the eval harness (`library/tools/speech_eval.py`, boundary errors
against a gold reference) and never reached the transcript, so nothing
downstream could tell a good alignment from a bad one.

**Added.** `library/tools/alignment_quality.py` reads the recording
the alignment claims to time and reports, per word, whether the
recording supports the placement:

- `corroborated` - the span carries signal close to this file's own
  speech level;
- `quiet` - the span carries signal, much quieter than this file's
  speech. **Nothing is decided**: a quiet word and a gap look the same
  to an energy measurement;
- `silent` - the span carries essentially nothing, and the placement is
  reported as CONTRADICTED.

A contradicted word is moved onto the nearest span the recording does
support, keeping its length, when one is close enough to be the same
word; every repair is recorded with the distance it moved. A word with
nothing supported nearby is left alone and reported. The floor is a
RATIO against this recording's speech level, not a level carried in
from another room, and a recording too flat to tell a word from room
tone is `unverified` and publishes nothing.

**Where it connects.** `hybrid_transcription.transcribe_and_align`
runs the check on the same audio the aligner used; step 1.04's batched
path runs it per clip on that clip's own clock, after the split back
from the concatenation; `source_memory.build_transcript_document`
publishes it in the M1 `instrument` block. An absent quality block
means "not checked", which is not "checked and clean".

**Still open.** The improvement has to be shown on held-out audio with
ground-truth intervals, and no benchmark was run here (the captain's
rule). The live lane should re-run the ASR qualification's held-out
`mfa` condition over `docs/benchmarks/asr-qual-2026-10-16/windows.json`
with the check enabled and disabled, and compare
`boundary.start_median_abs_ms` and `boundary.end_median_abs_ms` in the
`speech_eval` reports. The gold reference is already banked; only the
second run is new.

---

## VI-029 - Evaluate speaker separation during overlapping speech

**Already done.** Two arms and one scorer, that never met:

- `library/tools/overlapped_speech.py` measures WHEN two voices sound
  at once, per camera, by the energy arm (`ISO_MARGIN_DB`,
  `ISO_WINDOW_S`, `MIN_OVERLAP_S`, all measured on
  `docs/benchmarks/2026-10-12-mu20-overlapped-speech.md`: precision
  0.80, recall 0.83 on constructed crosstalk).
- `library/tools/single_track_diarization.py` gives one label per
  window, and says so in its own docstring: overlapped speech is
  attributed to ONE speaker, and separation needs two labels per frame,
  which the method never claimed.
- `library/tools/diarization_eval.py` scores overlap-aware DER from
  turn rows, with a global speaker mapping, a collar, and the
  missed-speaker-change counts.

**What was missing.** The arm that MEASURES overlap produced spans
naming camera TRACKS; the scorer that grades separation reads turn
rows naming PEOPLE. Nothing turned the first into the second, so the
question this item asks could not be asked at all. And a whole-file
DER would have buried the answer: most of its denominator is the
seconds where one person spoke, which every diarizer gets right.

**Added.**

- `overlapped_speech.as_turn_rows` / `as_turn_rows_of` - the connector.
  The track each voice was heard on has to be mapped onto the person
  who spoke it, and that map is the CALLER's to supply: two cameras
  that both heard the same person are two tracks and one person. A
  track the map does not cover is left OUT rather than carried as a
  person.
- `diarization_eval.overlap_metrics` - the DER of the OVERLAP alone:
  the reference's regions where two or more people spoke at once. It
  shares one DER implementation with `diarization_metrics` (which
  gained an `overlap_only` flag), so there is one reading of the error
  rate and not two.
- `speech_measurements.diarization_der`'s `unsupported_overlap`
  refusal now names where the overlap-aware scorer lives.

**Still open.** The measured arms were compared on CONSTRUCTED
crosstalk. The baseline records that on the unmodified episode's 4.87
seconds of transcript-labelled overlap the per-camera arm measured zero
precision and zero recall, and that the ECAPA single-track arm reached
P 0.42 / R 0.36 and did not ship. The evaluator above can now score
both against an overlap-aware reference; the reference itself (a
labelled overlap set on natural speech) is still to be labelled.

---

## VI-030 - Improve cross-segment speaker continuity and speaker-to-camera association

**Already done, and measured.** Nothing was added for this item.

*Cross-segment continuity.*

- Within one recording, `single_track_diarization.diarize_track`
  returns speaker clusters whose turns span the recording and each
  carries one 192-d ECAPA centroid, so a voice keeps its identity
  across the segments of that file. Its own eval measured DER 0.02-0.09
  against 0.34-1.14 single-label baselines.
- Across recordings, `person_entity.match_voices_across_sources` and
  `join_voices_across_sources` merge a voice across cameras, mic
  feeds and rooms at `VOICE_MATCH_THRESHOLD = 0.45` with
  `VOICE_MATCH_MIN_SECONDS = 21.0`. Those are MEASURED numbers, not
  preferences: `library/tools/voice_identity_study.py` ran the
  cross-source FAR/FRR study over 12 sources and 3 people with labels
  taken from physical tracks, and
  `docs/CROSS_SOURCE_VOICE_MEASUREMENT.md` records the result - FAR 0
  and FRR 0 at every threshold from 0.30 to 0.65 for tracks of at
  least 21 seconds, and the decision that follows from it (a false
  merge corrupts a person's whole speech history, so the threshold is
  set to keep FAR at zero).
- The Project Model carries the links: `project_model_evidence`
  migrates voice identity links and their CONFLICTS
  (`_migrate_voice_matches`) and speech-to-face links
  (`_migrate_speech_face_links`).

*Speaker to camera.*

- `library/tools/asd_lr.py` emits `active_speaker_score` observations
  carrying the raw LR-ASD score, not a calibrated confidence.
- `cross_modal_grounding.add_active_speaker_candidates` projects them
  into candidate voice-to-visible-person relations, joined only through
  a complete measured transform path whose accumulated uncertainty is
  within the caller's bound, and expressed on the visible camera's
  clock.
- `evidence_to_edit._speaker_camera_mapping` maps a speaker to a
  camera only when a measured speaking span carries BOTH the voice and
  face-track identities and a measured face frame overlaps that span on
  the same camera asset. Otherwise it reports `candidate` (the LR-ASD
  suggestions, labelled "suggestions, not verified speaker-to-camera
  mappings") or `abstained`.
- `library/tools/speaker_identity.py` carries the named-on-screen half:
  who speaks, which lower third belongs to them, and what the project
  declared about them.

**Still open.** The 2026-10-16 baseline records cross-recording
speaker identity as unmeasured for ITS episode, which the 2026-10-12
study answers in general but not for that material. A live run of the
study's own manifest over a fresh set of sources would close it.

---

## VI-031 - Detect retakes, repeated lines, false starts, and candidate preferred takes

**Already done.** Nothing was added for this item.

- `library/tools/retake_scan.py` finds false starts, paraphrased
  restatements and verbatim repeats, and reports them. It carries three
  guards so an emphasis repeat survives: the two instances must be the
  same speaker, the first must end before the second begins, and an
  abandoned telling must be incomplete (no terminal punctuation). It
  never names a winner.
- `library/tools/speech_take.py` types the relation between two
  utterances - `identical`, `repeated`, `rephrased`, `abandoned_first`
  - over transcript words only, keeps BOTH instances, and never drops
  one. A rephrased link needs a shared ordered run of three raw tokens,
  because a two-token run is not a distinctive-text claim.
- `library/tools/take_selection.py` ranks the preserved take instances
  under an EXPLICIT FI-22 `EvaluationPolicy`: requirements remove
  failing candidates, preferences rank the survivors by the count of
  passing rules, ties remain ties, and no delivery attribute (pause
  count included) gets an implicit preference. With no policy or no
  evidence it fails closed rather than ranking. `take_pick_preview.py`
  previews the choice.

The vocabulary in use is *alternate take*, *coverage group* and
*telling*. There is no `preferred_take` or `duplicate_take` concept
under those names, and none is needed: "candidate preferred takes" is
what `rank_take_instances` returns.

**Still open.** Nothing structural. The policy that says which
delivery wins is FI-19's and the model's, and it needs an editor.

---

## VI-032 - Identify edit-safe versus linguistically important pauses

**Already done.** `library/tools/speech_take.py` measured every gap
between spoken words and deliberately refused to judge it: "a 40 ms
hesitation and a 4 s silence are both gaps with durations, and judging
them is the consumer's job". That was right while nothing could judge
them with evidence.

**Added.** `library/tools/pause_class.py` reads a gap for four markers,
none of which is taste:

| marker | what was measured |
|---|---|
| `disfluency_lead` | the word right after the gap is a filler or an aborted word - the gap is the hesitation it was waiting for |
| `clause_final` | the text before the gap ends a sentence - closing the gap runs two sentences together |
| `turn_change` | the speaker after the gap is not the speaker before it |
| `event_occupied` | a measured non-speech event occupies the gap, so the gap is not empty |

The verdict is one of three, and the third is the point:
`linguistically_important` when a marker was measured; `edit_safe` when
no marker was measured **and every measurement that could have
produced one ran**; `undecided` otherwise. A gap between two
utterances needs speaker attribution before it can be called safe,
because that is where a change of voice would hide; a gap inside one
utterance cannot hide one, so it does not. The coverage is read off the
evidence that was passed, so a caller cannot claim a measurement it did
not make.

**Where it connects.** The migrated `speech.pause` observation carries
the reading next to the gap, so the Project Model holds the reason a
gap matters and not only its length.
`evidence_contract`'s description of that measurement type said
"judging gaps is the reader's job", which is no longer true of the
reading and still true of the cut, so it now says both.
`pause_class.speakers_from_voice_tracks` turns the diarization lane's
measured voice tracks (the M1b slot) into the per-utterance speaker
attribution the reading needs.

**Still open.** The reading needs a human-labelled pause inventory to
be checked against, on the same ground-truth-interval discipline the
alignment arm used. None was run here.

---

## VI-033 - Detect relevant non-speech events, including impacts, applause, laughter, music changes, and environmental sound

**Already done.** `library/tools/analysis/sound_event_pipeline.py`
measures TIMED non-speech events per clip with PANNs
`Cnn14_DecisionLevelMax` over the AudioSet vocabulary at 100 frames per
second, and withholds the voiced-speech classes because the transcript
times speech to the word. `library/tools/sound_events.py` is the single
reader of that shape. `library/tools/audio_classification.py` carries
the per-clip ambience type and music genre. The music structure
(`music_section.py`, `music_sections.py`) carries the sections of a
chosen bed.

**What was missing.** "Relevant" had no roster: the vocabulary was
AudioSet's 527 labels and nothing said which of them an editor cuts
on. And a music CHANGE - the thing the punch list names - was not a
measurement at all: the music class was measured as spans, and a
transition between "music" and "no music" was nobody's job.

**Added.** `library/tools/sound_class.py` is the roster of the classes
an editor cuts on - `impact`, `applause`, `laughter`, `music`,
`environmental` - with the AudioSet labels each covers. The labels are
the producer's own vocabulary, verbatim; a test asserts every one of
them is a label `panns_inference` really emits. A label the roster
does not cover is still reported, under `unclassified`: the roster is a
reading, not a filter.

A music change is read off the music class's spans: the class starting
where there was none, or stopping where there is none. A span that
runs to the end of the clip has NO measured stop, and one that starts
at the first instant has NO measured start, because the recording does
not say what was there before it.

**Still open.** The music bed's SECTION change inside continuous music
is a different measurement and stays where it is
(`music_section.py`, `audio_interval_evidence.py`).

---

## VI-034 - Improve acoustic event localization and distinguish absent from uncertain sound classes

**Already done.** The producer's operating points are measured
(`FRAME_THRESHOLD`, `MIN_DURATION_SECONDS`, `MERGE_GAP_SECONDS`), the
Project Model distinguished an unmeasured M5 slot from a measured empty
one at the FILE level, and `library/tools/audio_interval_evidence.py`
carried the typed interval vocabulary for music and environmental
audio - including the `resolution_bound` / `unquantified` uncertainty
kinds.

**What was missing.** Two things.

*Absent versus uncertain, per class.* A clip whose events were MEASURED
and found no laughter is a MEASURED ABSENCE of laughter. A clip whose
events were never measured has NO ANSWER about laughter. The reader
gave those two the same answer, so an editor was told there is no
laughter in a clip nobody listened to.

*Localization.* The sound events never reached the typed interval
evidence, so they carried no boundary uncertainty at all.

**Added.**

- `sound_class.class_reading` gives each class one of three states -
  `present`, `absent` (measured over the whole clip, nothing reached
  the threshold) and `uncertain` (the measurement did not run, with the
  reason travelling with the answer).
- The Project Model gains one observation per sound class
  (`audio.sound_class`), saying present, absent or uncertain, with
  `COMPLETE` coverage for a measured absence and `UNAVAILABLE` for a
  class nobody measured. A superseded or stale producer is UNCERTAIN,
  not absent: the measurement ran, but this is not the measurement the
  pipeline would make today.
- `audio_interval_evidence.sound_interval_evidence` puts the measured
  events into the typed interval layer with the boundary uncertainty
  the producer actually has - a gap under `MERGE_GAP_SECONDS` was closed
  into one event, so the reported extent may overstate the burst that
  produced it. The producer's frame rate became a constant
  (`FRAME_RATE_HZ = 100.0`) instead of prose.
- `view:soundevents` carries the class reading per block, so a planner
  reads "no laughter in this block" as a finding and "nobody measured
  this clip" as an absence of an answer.

**Still open.** The class reading is a mapping over labels the
reference sets already carry, so the held-out comparison needs no new
labelling - only a run over the sound-event sets banked under
`docs/benchmarks/`.

---

## VI-035 - Preserve the original audio reference and alignment when speech-based edits are applied

**Already done.** Nothing was added for this item. The mechanisms:

- `library/tools/audio_preserving_replacement.py` - a picture
  substitution keeps the ORIGINAL audio: the proposal must name the
  original and replacement spans, the bad-picture evidence, the
  synchronization evidence and a verified continuity verdict, and the
  source and timeline spans must preserve normal-speed sync to within
  one frame. The applied record carries `audio_source`,
  `audio_source_start`, `audio_source_end` and `replacement_source_span`,
  and the original audio is split onto its own clip so its file and
  source times survive the swap.
- `library/tools/dialogue_cleanup.py` - a cleaned stem carries the
  original audio reference on its record: `source_file`, `source_in`,
  `source_out`, `timeline_in_frame`, `timeline_out_frame`. The stem IS
  the played range, so the clip's media reference is rewritten and its
  source start resets to zero while the timeline it plays is untouched.
- `library/tools/edit_correction.py` - a verified trim keeps the
  alignment: the words are SOURCE-ANCHORED, so they ride the ripple
  untouched, the words that fell outside the trimmed span are dropped,
  and a cut that would fall inside a sounding word is REFUSED rather
  than eating speech.
- `library/tools/source_clocks.py` - the source clock is exact:
  rational frames per second and the audio sample rate, against the
  Project Model's 1000 Hz millisecond clock, with no float rounding.
- `library/tools/spine_contract.py` - every block carries `clip_id`,
  `source_start`, `source_end`, `word_timestamps` and
  `alignment_method`, and a speech block with empty word timings or a
  null alignment method is refused at the gate.

**Still open.** Nothing structural.

---

## The live evidence this lane needs

No benchmark was run here, on the captain's rule that the suite and the
benchmarks are not run locally, and no live Resolve run was made -
that belongs to the live lane. The measurements that would close each
item:

| Item | What the live lane should run |
|---|---|
| VI-028 | the ASR qualification's held-out `mfa` condition twice, with the alignment check on and off, and compare `boundary.start_median_abs_ms` / `boundary.end_median_abs_ms` |
| VI-029 | label a natural overlap set on real speech, then score `diarization_eval.overlap_metrics` against it - the evaluator exists; the reference does not |
| VI-030 | the cross-source voice study's own manifest over a fresh source set |
| VI-031 | nothing structural; the policy is the editor's |
| VI-032 | a human-labelled pause inventory to check the reading against |
| VI-033/034 | a run over the sound-event sets already banked under `docs/benchmarks/` |
| VI-035 | nothing structural |
