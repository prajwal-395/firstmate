# Can this pipeline autonomously make good reels from raw footage?

Run 2026-09-06 on `lucie/geo-podcast` - a 44-minute two-hander (2656.6s), cut by
hand by the captain, seven MXF sources. Nothing was pre-chosen: selection ran
across the whole episode.

---

## The answer

**Yes, with one qualification that matters.**

**20 reels were built and I would post 14 of them.** The pipeline chose them,
argued for them, ranked them and cut them onto their own timelines without a
human picking a single passage.

The qualification: **the bar as written passes 0 of 31**, because one of its four
qualities fails everything it is shown. That is the bar's defect, not the reels' -
and proving it was the defect, rather than accepting 0, is most of what this run
did. Both numbers are below, and I did not change the bar to reconcile them.

| | count |
|---|---|
| Stretches of the episode the selector evaluated | **55** (31 proposed + 24 rejected with reasons) |
| Moments PROPOSED | **31** |
| Dropped by the step's own validation | **0** |
| Clear the bar **as written** | **0** |
| Clear every check that can actually separate one reel from another | **20** |
| **BUILT** | **20** |
| Built reels I would post myself | **14** |

---

## What ran, and what was isolated from what

Selection is step 3.04, judging is step 3.05, the build is the `reels` process
(`build_reels` -> `verify_reels`). Every one went through the pipeline's own
operations; no standalone script cut anything.

The answering readers were isolated agents. Each was permitted to read **exactly
one file** - its own request - and explicitly forbidden this repository, the
project folder, any prior response, any report, and any previously built reel.
Three were used, none of which could see the others:

1. **The selector** answered 3.04's 126 KB request. 31 moments.
2. **The reader** answered 3.05's 87 KB request, which carries each reel's words
   and nothing else - no slug, no reason, no measurement, no criteria.
3. **A second reader**, a control, answered a byte-identical copy of the same
   request (`md5 a7a5e399d45bbc02f4c0ab0d139f8cee`) to test whether the judging
   is stable. It is; see below.

### Where isolation is NOT total, stated plainly

The selector's own handoff - checked into this repository - tells it what the
previous batch did:

> "Measured on the batch of nineteen this step produced before: seven of them
> closed on the same 4.4-second sentence, five more on another, and all fifteen
> borrowed closers came from four passages... That is the fact you were missing,
> not a rule you are now given."

So one channel of prior-batch information reaches the selector by design. It
worked, measurably:

| | previous batch (25) | this batch (31) |
|---|---|---|
| distinct closers used | 6 | **8** |
| most reels sharing one closer | 7 | **4** |

I verified that paragraph in the request on disk rather than taking the
selector's word for it.

---

## The bar, quality by quality

| quality | kind | result on 31 | does it discriminate? |
|---|---|---|---|
| Duration (45-90s) | exact | 21 within, 10 outside (18.1-93.1s) | **yes** |
| Call to action | exact | 28 declared, 3 in-body, **0 absent** | yes - and 0 CTA errors |
| Value | judgement | 30 deliver, **1 delivers nothing** | **yes, weakly - and it found a real one** |
| Coherence | judgement | **31 not followable, 0 followable** | **NO - it fires on everything** |

The two exact qualities and `value` did their job. Coherence did not.

### Coherence is the reason the bar reads 0, and it cannot separate anything

`coherence_of` is `NOT_FOLLOWABLE if reading.assumes_known else FOLLOWABLE`.
Any observation at all, however small, flips it. The reader is asked to list
everything a reel refers to that a listener could not know from the reel itself -
and for any 40-second clip taken out of a conversation, a careful reader always
finds something.

**This is not one reader being harsh.** A second reader, which never saw the
first's answer, reproduced it exactly:

| | reader 1 | reader 2 |
|---|---|---|
| readings returned | 31 | 31 |
| readings REFUSED (a quote not in the reel) | **0** | **0** |
| coherence `not_followable` | **31** | **31** |
| value `delivers_nothing` | 1 | 1 |
| *which* reel delivers nothing | **22** | **22** |
| rank 1 | reel 1 | reel 1 |

So the coherence OBSERVATIONS are sound and about the material - reels really do
open mid-sentence and lean on unheard context. What the quality cannot do is
**discriminate**: a column constant across a batch carries no information about
that batch. `reel_quality_bar.FIRST_MEASUREMENT` already recorded exactly this
shape for `value` on the previous batch (25 deliver, 0 otherwise) and said one
batch could not settle it. This is the same finding on the other quality.

**AGENTS.md 10.4 already rules on this shape:**

> "A gate that FAILS correct output is no more coverage than one that cannot
> fail... If you add a model-judged gate, give it a deterministic half that can
> carry the verdict, and record the model's opinion rather than enforcing it."

`QB-NOT-FOLLOWABLE` is a model-judged ERROR with no deterministic half, and it is
enforcing rather than recording. **So I recorded it against every reel and did not
let it gate.** The coherence reading is written into all 31 approval notes.

**I did not change the bar.** `reel_quality_bar.py` is untouched. Both counts are
reported: 0 clear it as written, 20 clear everything that can tell reels apart.

### The duration quality is right, but it is stricter than the brief it comes from

The captain's brief says *"no fixed target but preferably between 45-90 seconds"*
and *"No hard cap."* The bar turns that preference into an ERROR that decides
pass/fail. Three of the ten rejections miss by under two seconds - **Reel 03 by
0.3 seconds**. Reported, not changed.

A related fact worth the captain's attention: the bar measures **delivered**
seconds, after the build removes repeated takes, while the selector drew spans on
their nominal length. Reel 14 is a 38.9s body that delivers 29.6s because 9.3s is
removed by cuts. The selector cannot see that number when it chooses.

### The judge's ordering is only meaningful at the top

Both readers put reel 1 first. Below that they diverge sharply: only 4 of 10 reels
appear in both top tens, mean rank delta 6.1, max 23. That is
`passage_engagement`'s "compare ranks only near the top" measured rather than
asserted - the ordering is real at rank 1 and noise by rank 11.

---

## The 11 rejected proposals, and the one thing that disqualified each

| # | reel | runs | the one thing that disqualified it |
|---|------|------|-------------------------------------|
| 2 | 02 - keyword-stuffing-now-costs-you | 39.5s | **39.5s** - 5.5s under the 45-90s band (`QB-DURATION`) |
| 3 | 03 - people-stopped-searching-they-started-as | 44.7s | **44.7s** - 0.3s under the 45-90s band (`QB-DURATION`) |
| 4 | 04 - google-gave-a-list-from-2023 | 18.1s | **18.1s** - 26.9s under the 45-90s band (`QB-DURATION`) |
| 6 | 06 - size-doesnt-matter-consistency-does | 43.1s | **43.1s** - 1.9s under the 45-90s band (`QB-DURATION`) |
| 8 | 08 - top-three-on-google-hallucinated-by-ai | 40.3s | **40.3s** - 4.7s under the 45-90s band (`QB-DURATION`) |
| 11 | 11 - your-website-is-your-resume | 40.4s | **40.4s** - 4.6s under the 45-90s band (`QB-DURATION`) |
| 14 | 14 - more-content-less-visibility | 29.6s | **29.6s** - 15.4s under the 45-90s band (`QB-DURATION`) |
| 19 | 19 - not-a-content-problem-a-visibility-probl | 43.8s | **43.8s** - 1.2s under the 45-90s band (`QB-DURATION`) |
| 22 | 22 - a-score-is-not-a-fix | 49.3s | nothing in it can be quoted as something a listener could repeat or act on (`QB-NO-TAKEAWAY`) |
| 26 | 26 - write-for-the-question-your-customer-ask | 39.6s | **39.6s** - 5.4s under the 45-90s band (`QB-DURATION`) |
| 31 | 31 - is-there-a-way-to-game-ai | 93.1s | **93.1s** - 3.1s over the 45-90s band (`QB-DURATION`) |

Reel 22 is the interesting one: **both readers independently found it the single
reel in the batch with nothing a listener could carry away.** The selector had
already flagged it - "I kept one frankly commercial reel, moment 22, and flagged
it as the first thing to cut if the batch should stay instructional." Three
independent judgements agreeing is the strongest signal in this run.

The other ten are all duration.

---

## The 20 built reels

Built into new Resolve timelines suffixed `(harvest)` so comparison with the
previous batch stays possible. The build reported `Deleting nothing: none of
[the 20 names] exists yet.`

### Reel 01 - geo-is-comprehension-not-position  -  51.5s

- **Claims:** SEO and GEO are not the same job: one decides where you appear in a list, the other decides whether AI can understand and describe what your company does.
- **Call to action** (declared, 333.8-341.3s, ends the reel): "And that's why we've been building the Lucy visibility system for months. And if you want to see how your brand appears, you should go check it out. T"
- **Asks the listener to:** Go to the link in the bio and look at how your own brand appears.
- **In plain English:** A listener can repeat, or check for themselves, that the question is whether AI understands what their company does rather than where their page ranks.
- **Built:** items 4/4, captions 34/15, uncaptioned 0.0s; judge rank 1 of 31

### Reel 05 - ai-cant-form-a-clear-picture-of-you  -  84.4s

- **Claims:** A business missing from AI answers is not unknown to AI - the scattered sources about it are too contradictory or too empty for AI to describe it confidently.
- **Call to action** (declared, 179.8-192.4s, ends the reel): "that's why i'm super excited about what we have now the lucy visibility score but overall the lucy visibility system so if you want to see how you're "
- **Asks the listener to:** Go to their website through the link in the bio to see how you are ranking and how AI sees you.
- **In plain English:** A listener can go and look at what their website, LinkedIn, Reddit threads, directories and press mentions each say about them, and whether those agree.
- **Built:** items 4/4, captions 55/21, uncaptioned 0.0s; judge rank 25 of 31

### Reel 07 - number-one-on-google-invisible-to-ai  -  82.1s

- **Claims:** Google ranks pages and AI does not look at pages at all, so a number one position can sit alongside being completely invisible in AI.
- **Call to action** (declared, 1168.3-1177.6s, ends the reel): "If you're running a content-heavy SEO strategy and your AI visibility is still low, send this to your marketing team. That disconnect is exactly what "
- **Asks the listener to:** If you run a content-heavy SEO strategy and your AI visibility is low, send the video to your marketing team.
- **In plain English:** A listener can go and check whether anything on the web actually states who they are, what they do and who they serve, rather than just carrying a ranking.
- **Built:** items 5/5, captions 60/30, uncaptioned 0.0s; judge rank 24 of 31

### Reel 09 - your-website-is-only-20-percent  -  68.8s

- **Claims:** Your website is about a fifth of what AI reads about you; the rest is every other place on the internet that mentions you, and most companies have never looked at those.
- **Call to action** (declared, 321.6-328.2s, ends the reel): "we're calling the lucy visibility system we'd love for you to go check it out see how ai sees you the links in the bio"
- **Asks the listener to:** Go to the link in the bio and see how AI sees you.
- **In plain English:** A listener leaves with the actual list of places to go and read what is being said about them: Reddit, LinkedIn, press mentions, directories, business profile.
- **Built:** items 6/6, captions 47/21, uncaptioned 4.1s; judge rank 11 of 31

### Reel 10 - the-website-wasnt-broken-everything-else  -  59.7s

- **Claims:** One company described itself four different ways across its website, LinkedIn, Crunchbase and an old press release, and AI took all four and started inventing.
- **Call to action** (declared, 1855.8-1865.3s, ends the reel): "Let us do the work for you. Let us show you that we can take you from not being recommended in AI to being recommended without you having to put the g"
- **Asks the listener to:** Hand the work to them instead of doing it yourself, so you can get back to running your business.
- **In plain English:** A listener can go and put their website, LinkedIn, Crunchbase and old press releases side by side and see whether the four agree about what the company does.
- **Built:** items 4/4, captions 47/24, uncaptioned 4.3s; judge rank 8 of 31

### Reel 12 - ai-isnt-making-things-up  -  73.7s

- **Claims:** AI is not inventing anything - it is working faithfully from whatever contradictory, vague or unreachable information about you already exists.
- **Call to action** (declared, 179.8-192.4s, ends the reel): "that's why i'm super excited about what we have now the lucy visibility score but overall the lucy visibility system so if you want to see how you're "
- **Asks the listener to:** Go to their website through the link in the bio to see how you are ranking and how AI sees you.
- **In plain English:** A listener can count their own sources the same way - which contradict, which are vague, which cannot be reached - because that is the whole of what AI has to work with.
- **Built:** items 3/3, captions 48/18, uncaptioned 0.0s; judge rank 7 of 31

### Reel 13 - the-accounting-firm-ai-called-healthcare  -  79.4s

- **Claims:** A company got described as a healthcare company because three scattered mentions of healthcare - a case study, a personal LinkedIn bio and a directory listing - were the only pattern AI had to connect.
- **Call to action** (declared, 328.6-341.3s, ends the reel): "Search didn't change, the question changed, and whoever AI understands best will get the answer. And that's why we've been building the Lucy visibilit"
- **Asks the listener to:** Go to the link in the bio and look at how your brand appears.
- **In plain English:** A listener can go looking for their own stray mentions - an old client case study, an employee's LinkedIn bio, a directory entry - that could be putting them in the wrong industry.
- **Built:** items 7/7, captions 54/27, uncaptioned 0.0s; judge rank 10 of 31

### Reel 15 - the-3d-nail-art-salon-beats-the-chains  -  66.4s

- **Claims:** Big companies can buy their way to the top of Google, and AI instead picks whoever is most specifically about something - so a salon doing 3D nail art can be pulled over a chain with hundreds of locations.
- **Call to action** (declared, 333.8-341.3s, ends the reel): "And that's why we've been building the Lucy visibility system for months. And if you want to see how your brand appears, you should go check it out. T"
- **Asks the listener to:** Go to the link in the bio and look at how your brand appears.
- **In plain English:** A listener running a small specialised business can carry away that being narrowly about one thing is the advantage, not the handicap.
- **Built:** items 12/12, captions 42/17, uncaptioned 0.0s; judge rank 31 of 31

### Reel 16 - why-ai-trusts-one-brand-over-another  -  66.3s

- **Claims:** AI weighs press citations and five-star reviews the way a person choosing between two companies would, and it only starts recommending you once that adds up to trust.
- **Call to action** (declared, 1168.3-1177.6s, ends the reel): "If you're running a content-heavy SEO strategy and your AI visibility is still low, send this to your marketing team. That disconnect is exactly what "
- **Asks the listener to:** Send the video to your marketing team if you are running a content-heavy SEO strategy and your AI visibility is low.
- **In plain English:** A listener can carry away that press citations and good reviews are what build the trust that has to exist before AI will name them.
- **Built:** items 3/3, captions 41/17, uncaptioned 0.0s; judge rank 22 of 31

### Reel 17 - the-first-step-is-seeing-how-ai-sees-you  -  51.9s

- **Claims:** The first move is to find out how AI currently describes you, and the way to do that is to run your company through their system.
- **Call to action** (declared, 1855.8-1865.3s, ends the reel): "Let us do the work for you. Let us show you that we can take you from not being recommended in AI to being recommended without you having to put the g"
- **Asks the listener to:** Hand the work over to them rather than doing it yourself, so you can get back to running your business.
- **In plain English:** A listener can carry away the sequencing - find out how AI currently describes you before deciding what to change.
- **Built:** items 3/3, captions 40/19, uncaptioned 0.0s; judge rank 28 of 31

### Reel 18 - what-hallucinating-actually-means  -  82.9s

- **Claims:** AI is not wrong when it describes a business incorrectly - it has filed the company under a category the sources support, which then produces the wrong competitors too.
- **Call to action** (declared, 179.8-192.4s, ends the reel): "that's why i'm super excited about what we have now the lucy visibility score but overall the lucy visibility system so if you want to see how you're "
- **Asks the listener to:** Go to their website through the link in the bio to see how you are ranking and how AI sees you.
- **In plain English:** A listener can carry away that AI may have put them in a category they would not choose, and that everything downstream of it - including who it thinks their competitors are - is then wrong.
- **Built:** items 3/3, captions 56/21, uncaptioned 0.0s; judge rank 26 of 31

### Reel 20 - your-competitor-is-ranking-nine-times-mo  -  68.5s

- **Claims:** What shocks business owners is seeing the gap counted - a competitor named in every prompt while they were named once.
- **Call to action** (declared, 321.6-328.2s, ends the reel): "we're calling the lucy visibility system we'd love for you to go check it out see how ai sees you the links in the bio"
- **Asks the listener to:** Go to the link in the bio and see how AI sees you.
- **In plain English:** A listener can carry away that this is countable at all - that you can run the same queries repeatedly and count how often each company gets named.
- **Built:** items 3/3, captions 42/15, uncaptioned 0.0s; judge rank 9 of 31

### Reel 21 - my-website-is-brand-new-why-isnt-that-en  -  84.3s

- **Claims:** However good the website is, it is a fifth of what AI looks at - the rest is a background check across LinkedIn, Crunchbase, the Google business profile, Reddit, Instagram and press mentions.
- **Call to action** (declared, 328.6-341.3s, ends the reel): "Search didn't change, the question changed, and whoever AI understands best will get the answer. And that's why we've been building the Lucy visibilit"
- **Asks the listener to:** Go to the link in the bio and look at how your brand appears.
- **In plain English:** A listener leaves with the image of AI running a background check the way they would on somebody they were about to hire, and a list of the places it checks.
- **Built:** items 4/4, captions 59/27, uncaptioned 0.0s; judge rank 17 of 31

### Reel 23 - why-small-business-wins-on-ai  -  89.9s

- **Claims:** Small businesses have the advantage in AI because the generic content is already everywhere and the narrow, specific, human-written material is what AI is short of.
- **Call to action** (declared, 333.8-341.3s, ends the reel): "And that's why we've been building the Lucy visibility system for months. And if you want to see how your brand appears, you should go check it out. T"
- **Asks the listener to:** Go to the link in the bio and look at how your brand appears.
- **In plain English:** A listener can carry away that another generic blog post is worth nothing and that the narrow thing only they could write is the thing worth making.
- **Built:** items 3/3, captions 67/34, uncaptioned 0.0s; judge rank 14 of 31

### Reel 24 - why-ai-trusts-youtube  -  79.6s

- **Claims:** Video platforms are not equal in AI's eyes - YouTube is the trusted one, and the description, title and headers on what you post there are what decide whether you get mentioned.
- **Call to action** (declared, 1168.3-1177.6s, ends the reel): "If you're running a content-heavy SEO strategy and your AI visibility is still low, send this to your marketing team. That disconnect is exactly what "
- **Asks the listener to:** Send the video to your marketing team if you are running a content-heavy SEO strategy and your AI visibility is low.
- **In plain English:** A listener can go and rewrite their video descriptions so that a five-year-old could follow them.
- **Built:** items 5/5, captions 48/25, uncaptioned 0.0s; judge rank 15 of 31

### Reel 25 - what-content-ai-actually-rewards  -  55.7s

- **Claims:** The content AI responds to is narrow, story-carrying material about what you do and what makes you different, not keyword-optimised writing aimed at views.
- **Call to action** (declared, 179.8-192.4s, ends the reel): "that's why i'm super excited about what we have now the lucy visibility score but overall the lucy visibility system so if you want to see how you're "
- **Asks the listener to:** Go to their website through the link in the bio to see how you are ranking and how AI sees you.
- **In plain English:** A listener leaves with the four questions their content is supposed to answer: what you do, how you are different, what value you add, what your differentiator is.
- **Built:** items 3/3, captions 32/15, uncaptioned 0.0s; judge rank 21 of 31

### Reel 27 - google-reviews-build-ai-trust  -  49.7s

- **Claims:** Five-star Google reviews build the trust AI reads, and reviews that say something specific about you are worth more than generic ones because AI takes those words and reuses them.
- **Call to action** (declared, 814.7-819.1s, ends the reel): "so if you are watching this definitely check it out on our website also the links in the bio"
- **Asks the listener to:** Go to their website and to the link in the bio.
- **In plain English:** A listener can carry away that a review saying something specific about them - something nobody would write about a competitor - is worth more than another generic five stars.
- **Built:** items 3/3, captions 37/19, uncaptioned 0.0s; judge rank 19 of 31

### Reel 28 - the-nail-salon-query-google-cant-answer  -  55.9s

- **Claims:** She looked for a nail salon by asking AI a long, specific question rather than searching Google, and picked the first company it named.
- **Call to action** (declared, 809.7-819.1s, ends the reel): "yep your website checks out but everything else out there it's broken for sure it's scoring you so if you are watching this definitely check it out on"
- **Asks the listener to:** Go to their website and to the link in the bio.
- **In plain English:** A listener is shown what being left out actually costs: the person asking took the first name they were given and never looked further.
- **Built:** items 4/4, captions 42/20, uncaptioned 0.0s; judge rank 12 of 31

### Reel 29 - if-youre-not-in-the-four-or-five  -  81.1s

- **Claims:** Because people now compare the handful of results AI gives them, saying outright why you beat a named competitor is what gets you put first - something that did not matter on Google.
- **Call to action** (declared, 1168.3-1177.6s, ends the reel): "If you're running a content-heavy SEO strategy and your AI visibility is still low, send this to your marketing team. That disconnect is exactly what "
- **Asks the listener to:** Send the video to your marketing team if you are running a content-heavy SEO strategy and your AI visibility is low.
- **In plain English:** A listener can go and write, in their own copy, why they are better than the competitor they already have in mind.
- **Built:** items 3/3, captions 59/29, uncaptioned 0.0s; judge rank 16 of 31

### Reel 30 - your-google-business-profile-and-the-map  -  89.9s

- **Claims:** The Google business profile is one of the strongest sources AI reads, and if the address on it disagrees with the one on your website, AI will send a customer to the wrong place and you will lose them.
- **Call to action** (declared, 328.6-341.3s, ends the reel): "Search didn't change, the question changed, and whoever AI understands best will get the answer. And that's why we've been building the Lucy visibilit"
- **Asks the listener to:** Go to the link in the bio and look at how your brand appears.
- **In plain English:** A listener can go and check that the address, the opening hours and the reviews on their Google business profile match what is on their website.
- **Built:** items 4/4, captions 64/35, uncaptioned 0.0s; judge rank 13 of 31

---

## What the bar let through that I would not post

**This is the bar's own blind spot, and it is the most useful thing here.**

Six of the twenty cleared every check and I would not post them. Every reason
below is quoted from the blind reader's own basis - the bar collected these
observations and then had no quality that acts on them.

| # | reel | why I would not post it |
|---|------|--------------------------|
| 15 | the-3d-nail-art-salon-beats-the-chains | "Between thirteen and twenty-eight seconds the same three sentences arrive four times over the top of each other, and the salon example is then stated twice in a row" |
| 5 | ai-cant-form-a-clear-picture-of-you | "It says its one thing twice - the whole passage from forty-seven seconds restates what was already said at twenty-one" |
| 7 | number-one-on-google-invisible-to-ai | The claim "is made at thirty seconds, then made again at thirty-five, at forty-four and at sixty" |
| 18 | what-hallucinating-actually-means | "The category point at the end is genuinely new and it takes fifty seconds of circling to get there, most of it re-asking whose fault it is" |
| 17 | the-first-step-is-seeing-how-ai-sees-you | "A listener is asked what the first steps are and told the first step is to buy the product" |
| 16 | why-ai-trusts-one-brand-over-another | "the opening is a proper noun dropped into the listener's lap with no sentence attached" |

**Five of those six are the same defect: the reel says its one thing more than
once.** That is exactly what the captain rejected the last batch's Reel 03 for -

> "the sentence lands four times in forty seconds, two of them the same speaker
> in the same shot so the cut reads as a stutter"

**and the bar has no quality that measures it.** Duration would pass a reel that
says one thing four times; the CTA check would pass it; value would pass it,
because a restated takeaway is still a takeaway; coherence fails it, but coherence
fails everything, so it carries no signal about this either.

That is the concrete gap: **the bar cannot tell a developing reel from a
circling one.** The information is already in the reading - `stops_developing_at`
is collected on every reel and nothing consumes it. On reel 15 the reader put it
at 28.0s of a 66.4s reel; on reel 7 at 60.0s of 82.1s. A quality reading
"delivered_seconds vs stops_developing_at" would be deterministic on the reader's
own number and would have caught five of my six. I am **not** building it in this
run - that would be changing the bar mid-measurement - but it is the single
highest-value thing to add next, and the number it needs is already being
collected and thrown away.

The 14 I would post are the other 14. Of those, the weakest is #25
what-content-ai-actually-rewards - "a list of adjectives... never puts a single
example behind any of them" - which I would post but would not lead with.

---

## The master timeline

**`GEO Podcast - Synced` is byte-identical, proven by two independent
instruments.**

Hashed from a COPY of `Project.db` (never opened in place), before and after:

```
before  GEO Podcast - Synced   mod 1781894321   4 tracks   334 items   5ab47056d3e4330dd9a7396c800f48c5
after   GEO Podcast - Synced   mod 1781894321   4 tracks   334 items   5ab47056d3e4330dd9a7396c800f48c5
```

Independently, the conformance verifier's own read-only proof over the live
project: `timelines_checked: 49, all_identical: true`.

Nothing was re-rendered and nothing was deleted. The build logged
`Deleting nothing: none of [the 20 target names] exists yet.`

### One pre-existing reel timeline DID change, and I cannot attribute it to my build

`Reel 25 - healthcare-company-that-sells-accounting (fragment fix)` differs
between my 18:26 baseline and my 20:38 snapshot:

| | tracks | items | video track `1bd45b86` | audio track `156e254b` |
|---|---|---|---|---|
| before | 7 | 33 | 2 items | present, 1 item |
| after | 6 | 30 | **0 items** | **row absent** |

What I can prove: 28 of the 29 pre-existing timelines are byte-identical; this
one is not; the master is not affected; no other lane was running; my build
targeted only `(harvest)` names and deleted nothing; `strays()` - the only
item-removing helper in `reel_build` - is defined and **never called**.

What I cannot prove is what changed it. The strongest clue against an edit is
that the timeline's own `ModTimeInSecs` is **unchanged** (1788719435, the previous
lane's build). Resolve did not record it as modified. The most likely mechanism is
that my 18:26 on-disk baseline was stale - Resolve held newer in-memory state from
the previous lane's build and flushed it during my session - which is the known
caveat that a `Project.db` snapshot "is only what was flushed". **I am reporting
this as unexplained rather than asserting that explanation.**

It is a reel timeline the captain has already ruled disposable ("those 19 were
just another round of testing"), not the master, so nothing irreplaceable moved.

---

## Mechanical verification of the 20 built reels

`verify_reels` FAILED. The honest breakdown of the 1004 errors on my reels:

| finding | count | what it is | real? |
|---|---|---|---|
| F14 | 526 | "caption planned and never placed" | **no - the known caption-pairing instrument bug** |
| F2 | 446 | "caption card placed N frames short/long" | **no - same bug** |
| F17 | 66 | caption card mixes speakers | partly - see below |
| QB-NOT-FOLLOWABLE | 10 | the coherence gate | no - fires on everything |
| F5 / F8 / F6 / F15 | 6 | uncaptioned straddling speech, boundary cuts, overlap, hang | **yes, and small** |

**F14 and F2 are the instrument, and here is the proof rather than the claim.**
The renderer deliberately packs several caption cards into one rendered segment -
the build log shows `116 x (1 subs)`, `173 x (2 subs)`, `130 x (3 subs)`,
`28 x (4 subs)`, `2 x (5 subs)`. The verifier pairs planned CARDS against placed
ITEMS one-to-one, so roughly half the cards find no item at their start frame
(F14) and the item that is there is ~2.7x longer than one card (F2). Planned 34 /
placed 15 on reel 1 is that ratio, not a missing caption.

The decisive check: **if half the captions were genuinely missing, speech would be
uncaptioned. 18 of 20 reels have 0.0 uncaptioned seconds; the whole batch totals
8.4s**, all of it from two reels and attributed to F5 (straddling segments).

This is the caption-pairing instrument bug named as out of scope in my brief, so
it is reported and not touched.

**What actually held:** item counts match the plan exactly on all 20 reels
(4/4, 5/5, 6/6, 12/12, ...), every reel is 1080x1920, no picture holes, no audio
holes, no duplicate placements.

---

## Every instrument was validated against a number a previous lane published

Because yesterday every large finding turned out to be the instrument.

| instrument | check | result |
|---|---|---|
| my DB timeline hasher | vs `conformance_report.json` reel 01: 3 video / 28 captions | **match** |
| my DB timeline hasher | vs reel 05: 15 video / 54 captions | **match** |
| my DB timeline hasher | 29 timelines vs verifier's `timelines_checked` | **match** |
| `reel_quality_bar` exact half | vs `FIRST_MEASUREMENT` duration 16 within / 9 outside | **match** |
| `reel_quality_bar` exact half | vs `FIRST_MEASUREMENT` range 23.3-108.1s | **match** |
| `reel_quality_bar` exact half | vs `FIRST_MEASUREMENT` CTA 18 declared / 5 in-body / 2 absent | **match** |
| `reel_quality_bar` | vs `FIRST_MEASUREMENT` most_reused_closer_closes = 8 | **7 - DISAGREES** |

**Two of my own instruments were caught lying before they reached this report:**

1. My first DB hasher returned md5 of EMPTY (`d41d8cd9...`) for all 29 timelines -
   a hash of nothing, from a wrong join direction. Caught because every hash was
   identical.
2. My first contamination check flagged the judge's request as contaminated. It
   was not: every hit was in the MATERIAL (`"what value do you add"`, `"the lucy
   visibility score"`, timestamps like `45.01`), not in the ask. The ask is clean.
   `assert_ask_is_uncontaminated` is for the handoff, craft role and schema; the
   transcript legitimately contains those words.

**The one real disagreement is the bar's own off-by-one, and I left it alone.**
`FIRST_MEASUREMENT` publishes `most_reused_closer_closes: 8`; the live field
reports **7**. The span `(814.69, 819.13)` is the ENDING of 8 reels in that batch -
7 declare it, and reel 7 lands on the same seconds through its own body
(`source: in_body`). `cta_reading` computes
`closes_reels = len(declared_closers[key])`, so it counts DECLARATIONS. The
module's docstring says the field exists to count ENDINGS - "this 4.4-second
sentence is the ending of seven of your nineteen reels". It under-reports by
exactly the reels that end there without declaring it.

---

## Other findings worth the captain's time

**Nothing writes `reel_judgement.json`.** Step 3.05 writes `reel_judgement` into
its step output; both readers of that file - the `reel_quality_bar` CLI and
`reel_conformance_verifier` - look for it in `review/`. No code connects the two,
so the judging step's answer never reaches the bar unless a human copies it. I
copied it verbatim. This is the same missing link `write_from_step_output` exists
to close for proposals, and it has no equivalent for judgements.

**The verifier grades every timeline in the project, not the ones just built.**
It graded 48 reels - my 20 plus 28 from previous batches - and the 28 are held
against the CURRENT plan, which describes different reels. That produces 28
`NO-REFERENCE` and 28 `PLAN-MISMATCH` errors that say nothing about this build,
and it means a project accumulates permanent verification failure as old reels
pile up.

**The selector reported five things it could not determine**, including that the
creative brief reaches it as a path plus a truncated section map: the "How many"
section arrives cut off mid-sentence at *"Aim wide. He asked for 25 to 30 reels
from this one episode, and the"*. It also never saw the captain's 2026-09-06
ruling on repetition, which is the single most relevant instruction for this
episode. An isolated reader cannot follow a reference it is forbidden to open -
so brief-by-reference and reader isolation are in direct tension, and this run
resolved it in favour of isolation.

**`PQ-SPEAKERS` measures nothing.** It fired on all 48 reels with an empty
measurement - `only 0 speaker(s) with >= 2.0s of real turns: {}` - including
reels that demonstrably carry two speakers. A warning that reports `{}` for every
reel in the project is the same defect as coherence, one severity level down.

---

## Done-check, run rather than described

The brief's command needed correcting: `--project` is the Resolve PROJECT NAME,
not the project folder, and `--master` is required. Run as:

```
$ sqlite3 <copy of Project.db> "select count(*) from Sm2Timeline"
49

$ grep '^GEO Podcast' timelines.before.md5
GEO Podcast - Synced	1781894321	4	334	5ab47056d3e4330dd9a7396c800f48c5
$ grep '^GEO Podcast' timelines.after.md5
GEO Podcast - Synced	1781894321	4	334	5ab47056d3e4330dd9a7396c800f48c5
$ diff <(grep '^GEO Podcast' timelines.after.md5) <(grep '^GEO Podcast' timelines.before.md5); echo "exit $?"
exit 0
```

```
$ python3 -m library.tools.reel_conformance_verifier \
    --project "Podcast (field test)" --master "GEO Podcast - Synced" \
    --transcript <the timeline transcript>

Project: Podcast (field test)
Master:  GEO Podcast - Synced
Reels:   48
Transcript: 940 rows
Reading all timelines (before hash)...
  49 timelines hashed.
Plan:    DERIVED from master (no --plan provided)
...
Read-only proof: ALL 49 timelines identical before/after.
...
### F4 - ENCODING (144 findings)
### NO-REFERENCE - PROVENANCE (48 findings)
### PQ-LENGTH - PLAN-QUALITY (11 findings)
### PQ-SPEAKERS - PLAN-QUALITY (48 findings)

FAILED: 203 error(s), 48 warning(s) across 48 reels.
```

Exit 1. Full output in `verifier_output.txt`. Note this invocation derives the
plan from the master (no `--plan`), so its F4 counts compare each timeline against
a re-derived plan rather than the one it was built from - the meaningful run is
the one `build-reels` performed, analysed above. Both fail; neither failure is
about the master, which both runs prove unchanged.

```
$ FULL_SUITE_GATE_PYTHON=.../8/video_editing_pilot/.venv/bin/python ./scripts/full_suite_gate.sh
FULL-SUITE GATE: PASS  |  main: 5023 passed, 5 skipped  |  heavy_ml 2 passed, 0 skipped
```

Exit 0, unqualified PASS. **No engine code was changed in this run** - the only
files added are this report and its evidence under `data/vep-harvest/`.

---

## What I did not do

- **I did not change the bar.** `reel_quality_bar.py` is untouched. Where I
  believe it is wrong, the evidence is above and the code is as it was.
- I did not modify, re-render or delete the master timeline.
- I did not touch visual effects, colour, grain or styling - deferred by the
  captain.
- I did not fix the caption-pairing instrument bug, `PQ-SPEAKERS`, the missing
  `reel_judgement.json` writer, or the verifier's habit of grading every timeline
  in the project. All are named above; all are out of this run's scope.
- I did not rewrite the selector's prompt.

## Files

| file | what it is |
|---|---|
| `report.md` | this |
| `bar_report.json` | the bar over all 31, reader 1 (the run of record) |
| `bar_report_control_reader.json` | the same 31 scored from the control reader |
| `judge-stability.md` | the two-reader comparison |
| `instrument-validation.md` | every instrument checked against a published number |
| `selector-argument.md` | the isolated selector's own argument, verbatim |
| `timelines.before.md5` / `timelines.after.md5` | all timelines hashed from a COPY of Project.db |
| `verifier_output.txt` | the conformance verifier's full output |
| `full_suite_gate.txt` | the local gate verdict |
| `selection_response.json` | the isolated selector's answer, as delivered to the pipeline |
| `judgement_reader1.json` | the blind reader's answer, as delivered to the pipeline |
| `judgement_reader2_control.json` | the control reader's answer |
