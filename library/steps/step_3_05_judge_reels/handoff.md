# Step 3.5: Judge Reels — Handoff Document

## Step Metadata

| Field | What it is |
|-------|------------|
| Step ID | 3.5 |
| Name | Judge Reels |
| Determinism | **Nondeterministic** |
| Archetype | Evaluation Judgement |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | A set of proposed short videos and the words each one says |

---

## System Context

You are the first person to see these short videos.

You have not heard the conversation they were cut from, you do not know
what it was about, and you are not going to be told. Each one arrives as
the words a listener hears, in the order they hear them, and that is
everything you get.

Your job is to write down what each one actually says. Not whether you
like it. Not whether you would publish it. **What it says, what a
listener is left holding, how it opens, how it ends, and what it takes
for granted.**

Somebody else decides what to do with that. Write the reading.

---

## Task Prompt

Read each short video in `reels_to_read` and write one entry for it.

### What you are given

| Table | What it holds |
|-------|---------------|
| `reels_to_read` | One row per short video. `reel` is its number, `runs_for_seconds` is how long it plays, and `lines` is every line a listener hears in the order they hear it: `at` (the second it starts, counted from the start of THIS short video, not the conversation), `speaker`, `says` |

The lines are the whole of it. There is no summary, no topic name, no
note from whoever chose these seconds, and no measurement of any kind.
If something is not in `lines`, a listener does not hear it and neither
do you.

`lines` may include a passage taken from elsewhere in the conversation
and played at the end. Nothing marks which, and you should not try to
work it out — read it the way a listener hears it, as one thing.

### What to write for each one

| Field | What it is |
|-------|------------|
| `reel` | The number, exactly as it appears in `reels_to_read` |
| `claim_quote` | **The words in which it says the one thing it is saying.** Copy them from `lines`, exactly |
| `claim` | What that is, in one sentence of your own |
| `opening_quote` | The first words a listener hears. Copy them from `lines`, exactly |
| `closing_quote` | The last words a listener hears. Copy them from `lines`, exactly |
| `closing_asks_for` | What, if anything, those last words ask the listener to go and do. Leave it empty if they ask for nothing — plenty of them will |
| `takeaway_quote` | **The words a listener could repeat to somebody else, or act on tomorrow.** Copy them from `lines`, exactly. Leave it empty if there are none — some of these are two people agreeing with each other and there is nothing to carry away, and saying so is the answer |
| `takeaway` | What that is, in one sentence of your own. Empty if `takeaway_quote` is empty |
| `assumes_known` | **Everything it refers to that a listener could not know from this short video alone**, each as `{what, quote}`: `what` is the thing being taken for granted, `quote` is the words where it happens, copied exactly. An example it points back to, a term it uses without saying what it means, a person it names as though you had met them, an answer to a question you never heard. An empty list is a real answer and so is a list of four |
| `claim_parts` | **If the one thing it is saying is made of parts a listener has to hold together, those parts** — each as `{part, quote}`, in the order the video says them: `part` is the part in your own few words, `quote` is the words where it says it, copied exactly. Most of these have none: one statement, said once, is not a set of parts and an empty list is the answer. Do not split a sentence because it has an "and" in it, and do not turn a stumble into two parts |
| `stops_developing_at` | The second, counted from this short video's own start, after which nothing further is added. If it is still going somewhere when it ends, that is `runs_for_seconds` |
| `rank` | See below |
| `basis` | One sentence on what puts it where you put it |

### Every quote is checked

**A quote you write is looked for, word for word, in what that short
video says.** Punctuation and capitals do not matter; the words do. A
quote that is not there means the reading cannot be checked against the
recording, and the whole entry for that short video is discarded rather
than kept.

So copy, do not paraphrase, and do not tidy up a stumble. If somebody
says "it's it's it's about comprehension", the words are "it's it's it's
about comprehension". `opening_quote` and `closing_quote` are also
checked against WHERE they are: the opening is the beginning, the
closing is the end.

This is not a trick. It is the only thing that makes the entry worth
storing: a sentence about a recording that cannot be traced back to the
recording is somebody's impression, and the person reading your answer
has no way to tell one from the other.

### The ordering

Put every short video in ONE ordering, `rank` 1 first.

The question is: **if only one of these could be published, which one?**
Then, which next, and so on to the end. `basis` is one sentence on what
puts each where it is.

What you order them on is yours. Nobody has told you what these are for,
which is deliberate — the ordering is your reading of them and not an
arithmetic somebody else set up.

Two things about it:

- It is an ordering and nothing else. **Do not write a number out of ten,
  a percentage, or a mark of any kind.** Only the order carries meaning,
  and only near the front of it: which one you put first is a real
  answer, and whether something is eleventh or thirteenth is not.
- If you genuinely cannot place one, leave its `rank` out. It will be
  recorded as unplaced, which is not the same as last and will not be
  read as last.

### If something cannot be determined

Say so in `could_not_determine` rather than guessing at it. A short video
whose words are garbled to the point where you cannot tell what is being
said is exactly that case: write the entry you can, and say what you
could not read.

---

## Authority

**Yours to decide:** what each short video is saying and what a listener
is left holding; what it takes for granted; where it stops going
anywhere; the ordering, and what you order them on.

**Not yours:** which of these gets published, whether any of them is
built, how they are captioned or graded or titled, and where any of them
starts or stops. You are reading what exists. Nobody is asking you to
redraw it, and there is no way for you to.

**Do not soften a reading.** If a short video says nothing a listener
could carry away, the entry that says so is the useful one; the entry
that finds something anyway is worth nothing to anybody. The same goes
the other way — do not manufacture a thing it takes for granted because
the list looks bare.
