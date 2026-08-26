# creative_direction (step 2.01) - reasoning

Written before the answer. Request: `pipeline_output/llm_requests/creative_direction.json`
(prompt 5,656 chars; context 26,668 chars; schema 336 chars).

## 1. What I actually read

**Read in full, and used:**

- `transcript` (110 rows, ~112 lines of the context). This is where every decision below
  came from. It is the only part of the context that carries meaning rather than
  description. I read all 110 rows including the six long run-on rows (clip_011 @92-118,
  clip_013 @30-60 and @61-85, clip_016 @0.7-28, clip_017 @28-55 and @56-83) which is where
  the actual thesis of the piece turns out to live.
- `clip_catalog` (17 rows: duration + filename). Used to size the material and to join
  the transcript's `clip_XXX` ids to the semantic documents' file stems.
- `semantic_analysis_documents`, but only two of its six columns: `assessment.content_type`
  and `camera` (mode / framing / stability / movement). I tabulated those across all 17
  clips before deciding anything, because "which clips are talking heads and which are
  B-roll" is the one structural fact I needed and the transcript cannot tell me.
  Result: 10 `person_talking_to_camera`, 6 `scenery`, 1 `object_showcase`.

**Read and deliberately set aside:**

- `semantic_analysis_documents.scene` and `.analysis`. These describe only the OPENING of
  each clip - see section 4. I read them, confirmed they were not usable for a 188-second
  clip, and stopped relying on them.
- `assessment.usable_ranges`. Every one of the 17 clips reads `[[0, <full duration>]]`.
  A field where every value is "all of it" carries no information, so it changed nothing.
  I am not treating that as a defect - it may be literally true - but it did not help.
- `temporal_index`. 15 of 17 clips carry a single boundary `{"time": 0.0, "type": "start"}`.
  Two clips have exactly one scene change each (clip_002 @10.07, clip_015 @50.745). For a
  step whose job is "find the key moments", this contributed nothing at all.

**Could not read, because it is not there:**

- `prosody`. The whole section is one line: `not_measured: 17 of 17 clip(s) have no prosody
  measurement: parselmouth not installed`. See section 4.
- `creative_brief`. The prompt spends a whole section (~200 words) on what to do "when a
  `creative_brief` is provided in the input". No brief is in the context. I followed the
  prompt's own fallback: derive the direction entirely from the footage.

## 2. What the footage is, before I chose anything

One person, one afternoon into evening, in and around a parking lot / construction site /
urban block, filming himself on a phone. He is announcing that he is going to post a video
every single day, aiming at 100. Underneath that he is visibly and audibly uncomfortable
being filmed in public. The sun goes down over the course of the material.

## 3. What I considered, and what I rejected

The footage offers **four** threads, not one. Naming them all, because choosing between
them IS the decision this step exists to make:

**(a) The Casey Neistat anniversary.** "11 years ago today, casey neistat posted his very
first vlog" (clip_011 @34-48), "it lines up so nicely that this day 11 years ago was the
day that casey neistat posted his first vlog" (clip_017 @28-55).

**(b) The announcement / the number.** "i just want to post every single day" (clip_011
@63-71), "the goal for me is to get 100 of these videos out, this is the first one"
(clip_013 @61-85), "call this video one of 100" (clip_015 @29-50).

**(c) The internal one: paralysis, and posting as the cure.** "i get caught up in all the
numbers and metrics and the idea of succeeding that i just kind of enter a state of
paralysis where i don't do the thing in the first place, but just post the video"
(clip_011 @92-118). And the harder version: "i've been literally this week i've quit every
single day and the only reason i'm recording today is because i told myself this is the
last shot i got" (clip_017 @28-55).

**(d) The comedy of filming in public.** "i can feel the silent judgment of the people
behind me" (clip_011 @0.8-3.2), "i spent like 30 minutes trying to find a spot that i
could just kind of talk and no one would kind of look at me" (clip_011 @173-180), "now
there's people, so now i'm gonna whisper" (clip_011 @181-183), "oh, i'm so self-conscious"
(clip_011 @184-186).

**I took (c) as the spine.** It is the only thread with an actual arc inside 60 seconds -
symptom, cause, decision - and it is the only one that gives the viewer a reason to care
about a stranger's posting schedule. The line "even if it's bad, even if i hate it, i will
post it" (clip_011 @119-122) is the strongest sentence in 13 minutes of material and it is
the resolution of (c), not of anything else.

**I rejected (a), the anniversary, as the spine, and cut it to nothing.** Two reasons, and
the second is the one that decided it. First, it is a coincidence of date - the fact that
Casey Neistat posted on March 25th does not make this video about anything. Second, and
this is the real cost: it requires a name-drop. In a 60-second piece, a viewer who does not
already know who Casey Neistat is spends the next eight seconds not listening while they
work it out, and a viewer who does know now expects a video about Casey Neistat. I am
paying eight seconds for a fact that does not change the outcome. Cut.

I want to record the counter-argument, because it is not weak: in clip_017 the anniversary
and the paralysis arrive fused in one breath - the anniversary is the *excuse he used* to
break the paralysis, not decoration. An editor who kept (a) would be keeping the actual
causal mechanism. I am choosing comprehension over causality for a 60-second cut. If the
target were 3 minutes I would keep it.

**I rejected (b), the announcement, as the spine.** It is information, not a story. "I am
going to post every day" is a fact that takes four seconds to state and does not build. But
it cannot be cut either - the piece is unintelligible without it - so it goes in as the
connective tissue between the diagnosis and the resolution, not as a peak.

**I rejected (d), the self-consciousness, as the spine, and kept it as texture.** This was
the closest call and I nearly went the other way. (d) is the strongest thread VISUALLY -
it is the only one where what is on screen (a man wandering a parking lot, whispering,
retreating from passers-by) is the same thing as what is being said, and it hands you the
best cold open in the material for free. What killed it as a spine: it has no ending. He
is still self-conscious in the last clip. A thread that does not resolve is a texture, and
building 60 seconds on it produces a piece that stops rather than lands. So the hook comes
from (d) and the spine does not.

**On mood, I rejected "motivational".** That is the obvious label for "man commits to
posting daily" and it is wrong for this footage. He whispers, apologises for his orange
face, says "i really have no expectations", "even if i hate it", "this doesn't really
concern anyone except me". Calling it motivational would send step 2.4 hunting for
triumphant music and step 4.x for fast cuts, and both would fight the source. The honest
mood is self-deprecating and quietly resolved.

**Tension with the brand template, stated rather than resolved silently.** The constraints
block declares `energy_profile: "high"` and `music_genre: ["electronic", "upbeat"]`. The
footage is neither. I am going against both, and I think that is correct here for a
specific reason: `project.yaml` names no brand template, so this is the fallback
`default_brand`, whose values are a default nobody chose for this project rather than a
brand decision. I would not overrule a template the captain had actually selected. This is
flagged in `rationale` so the choice is visible rather than buried.

**On `target_energy` I checked the downstream reader, which is knowledge the prompt did not
give me and I should declare.** `library/tools/energy_reading.py` substring-matches this
free-text field into high / calm / moderate. "building" matches neither list and reads
`moderate`; "reflective" or "low" would read `calm`; any string containing "high" or "peak"
reads `high`. I want moderate - the piece has forward motion and a sunset, it is not a
calm-and-slow edit - so "building" is both the honest description and the value that
produces the reading I intend. I am recording this because an editor who did not know the
reader existed would plausibly have written "low to building" and quietly got `calm`.

## 4. What I was missing

- **All prosody, for all 17 clips.** The prompt asks me to identify the moments that MUST
  appear. I picked them from TEXT ALONE. I cannot hear which lines he lands and which he
  mumbles, where he pauses, where his voice drops. For a piece whose whole subject is a
  man's discomfort, vocal delivery is the single most relevant signal available and I had
  none of it. This is the largest hole in this decision by a distance.
  (Mechanically this is working as designed - AGENTS.md 10.1 says state the absence in one
  line rather than shipping 17 copies of an error, and that is exactly what I got. The
  reporting is right. The measurement is missing.)
- **Visual knowledge of my most important clip.** `semantic_analysis` describes clip_011 as
  `[0.0-18.9s] Outdoor urban area with a parking lot and construction site`. clip_011 is
  **188.578 seconds long**. Every line I chose from it sits between 63s and 122s - i.e.
  entirely outside the described window. I do not know what is on screen during the
  announcement, the diagnosis, or the resolution. I chose those moments blind to their
  picture. (This is the open issue #209 and this run is a clean instance of it: the defect
  lands on precisely the clip that matters most, because the longest clip is also the one
  he talked the most in.)
- **The `clip_XXX` <-> filename join is not stated anywhere in the context.** The transcript
  says `clip_011`; the semantic documents say `IMG_1816_v3`; the catalog says
  `IMG_1816.MOV`. Nothing in the prompt tells a reader these are the same clip. I inferred
  it from row order and from `188.578` appearing as the duration in both tables, and I
  verified the whole 17-row mapping that way before using it. A reader who did not check
  would silently attribute the wrong scene description to the wrong transcript.
- **No creative brief.** Noted above. The prompt asks me to honour a brief's intent and to
  document tension with it; there is no brief, so there is nothing to be in tension with,
  and the direction below is mine rather than the captain's.

## 5. Confidence

**High** on the narrative thread (c) and on the three key moments. Those lines are
unambiguous in the transcript and there is not a serious competing reading. This part was
easy and I want to say so plainly rather than dress it up.

**Medium** on mood and energy. "Self-deprecating and quietly resolved" is a judgement about
tone made from a transcript with no audio. If prosody had been measured and showed him
energetic and laughing rather than flat and hesitant, I would have moved toward "wry and
buoyant" and probably to `target_energy: "dynamic"`.

**Low-to-medium** on rejecting (d) as the spine. Reasonable editors differ here and I can
argue the other side. What would change my answer: if the picture showed the wandering as
genuinely funny - people visibly reacting, him ducking away - (d) becomes a comedy piece
and beats (c). I cannot see the picture (section 4), so I decided it on structure alone,
which is the weaker basis.

**What would change the whole answer:** a creative brief. If the captain has written down
what this series is, it outranks everything above, and I would rewrite this from it.
