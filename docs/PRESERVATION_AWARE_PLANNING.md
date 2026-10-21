# PLAN-006: preservation-aware edit planning

What a request may move, modelled BEFORE the write, and the conflicts an
agent sees before it moves it.

## The gap

The engine already judges preservation, but it judges it on the READ-BACK.
`library/tools/preservation_invariant.py` compares the state a write was
authorized against with the state it left behind, and calls every change it
cannot account for collateral. `library/tools/compound_transaction.py` asks
the same question before the write, but only about the work the REQUESTER
named: "you required this clip unchanged, and you also write to it" is
refused; "your ripple would move this clip" is not, because the feasibility
check asks what the request WRITES, not what the request REACHES.

Three defects fell out of that, and all three are visible in the corpus the
measurement runs:

1. **A request that names two items was read as naming one.** `clip.link`
   names the partner it links to and `clip.join` names the item it joins;
   the authorization scope read `unique_id` alone. The partner's link state
   was a change on an item the request had named, and the read-back called
   it unauthorized.
2. **Resolve's native ripple moved the speech and the overlays, and
   nothing said so.** `clip.remove` closes the gap with Resolve's own
   ripple, which is timeline-wide; the engine refuses that primitive
   outright (`edit_algebra._remove_clip_with_measured_retry`), so the agent
   learned that the operation was unavailable and not what it would have
   moved. The refused primitive is the only place the blast radius was
   named, and it named it in one sentence instead of item by item.
3. **A caption card left covering a picture that is gone was invisible.**
   No item changed, so no diff row records it, and the caption was simply
   wrong afterwards.

## The model

`library/tools/collateral_scope.py` computes, for one request against one
pre-edit state:

- the **authorized** set: the rows the request names and the intervals it
  declares;
- the **collateral**: every item the request may move, each with the ASPECT
  it moves in and the KIND of movement it is;
- the **conflicts**: the movements that are unauthorized, and the necessary
  movements the request does not carry.

### The five aspects

The punch list names them, and they are the only ones:

| aspect | what it is |
|---|---|
| `adjacent_items` | another item on the row, or the item's own content |
| `timing` | where an item sits, and how long it is |
| `linked_audio` | the partner of a link group, and the group's state |
| `captions` | a caption card, and whether it still covers what it covered |
| `effects` | a grade or a comp, and whether it rode along |

### The three kinds

| kind | who asked for it |
|---|---|
| `requested` | the request named the item, or the request IS a ripple and the item is on a row the request names |
| `necessary` | a declared rule implies it follows from a requested change: a linked partner moves with its partner, a caption card follows the picture it covers, an effect rides along with the item it is on |
| `unauthorized` | nothing in the request implies it: the change would touch work the request does not name, on a row the request does not name |

A `necessary` movement the request does not CARRY is a conflict too. The
caption card left covering a picture that is gone is not an unauthorized
modification - it is an unfinished one.

### The rules

The rules are the engine's own, not a second vocabulary:

- the DOMAIN of an operation is `edit_algebra.ALGEBRA`;
- whether it shifts timing is `edit_algebra.temporal_effect`;
- the REACH of a shift is declared per operation, because a temporal effect
  does not say how far the shift travels (`_REACH` in the module): Resolve's
  native ripple reaches every row, the engine's own scoped ripple reaches
  the row it names;
- the items an operation names are read from EVERY key it names them with
  (`named_targets`).

### The preference

"Prefer changes that affect only the authorized media or interval" is a rule
about which of several ways to do the same thing the engine should pick:
`prefer_least_collateral` orders the variants by unauthorized movements
first, then by necessary movements the request does not carry, then by
movements at all, and the first variant wins a tie so the requester's order
survives.

## Where it runs

`compound_transaction.preflight` is the ONE pre-write gate, and it now:

- computes the collateral scope and puts it in the result, so the agent
  reads what the request may move before it moves it;
- REFUSES a request whose own CONSEQUENCES reach work the requester
  required unchanged - the half of the check the feasibility pass could not
  see.

The refusal is narrow on purpose: only the approved work is an invariant
that refuses. Everything else is reported, because a report the agent can
read is what "visible before execution" means, and a gate that refuses
every request with a ripple would refuse the work rather than describe it.

## The measurement

`scripts/preservation_collateral_proof.py` runs a corpus of requests against
SCRATCH COPIES of a timeline and asks two engines the same question: what
did the write move, and what did the pre-write gate say before it?

The scratch copy is the canonical Resolve double
(`tests/resolve_double.py`), whose behaviour is measured against real
Resolve rather than assumed. Every request runs in a fresh project under a
temporary directory; nothing reads a captain's project, touches the
captain's media, starts a broker, or contacts Resolve.

```
python3 scripts/preservation_collateral_proof.py [--base <sha>] [--json]
```

`--base` names the commit the BEFORE measurement is taken from (the
merge-base with origin/main by default). The base tree is extracted to a
temporary directory and the SAME corpus runs against it, so the two numbers
are two engines, one corpus, one executor.

### What the corpus contains

Thirteen requests over one world: a picture row, a speech row, an overlay
row and a caption row, with one link group. The requests cover a local
delete, Resolve's native ripple, a ripple whose row the request names, a
link, an unlink, a delete under a caption card, a property write, a
disable, a marker, and four requests that require work unchanged.

### What it measured

| | before | after |
|---|---|---|
| unauthorized changes detected BEFORE execution | 0/5 | 5/5 |
| collateral changes named before execution | 0/13 | 13/13 |
| stranded caption cards named before execution | 0/3 | 3/3 |
| approved-work requests refused | 3/4 | 4/4 |
| unauthorized reported where none occurred | 0 | 0 |

The last row is the fidelity check: the model reported no unauthorized
movement that the scratch copy did not make, so the detection is not bought
with false refusals.

## The limits

- The ground truth is the OBSERVED diff of the scratch copy, classified by
  the harness's own reading of the request, not by the module under test.
  The two agree by construction only where the harness says so; where they
  disagree, the report says so too.
- The scratch copy is the Resolve DOUBLE. It reproduces Resolve's native
  ripple faithfully (the shift reaches every row) and it does NOT couple a
  linked pair's state the way Resolve does - deleting one of a linked pair
  removes both in Resolve and only one in the double. A corpus case that
  depends on that coupling would be measuring the double, not Resolve.
- The model's REACH table is a declaration, not a measurement of every
  operation. An operation whose temporal effect the engine declares but
  whose reach nobody measured is not allowed to claim collateral.
- The caption row is found by the NAME the row carries, or by the caller's
  declaration. A caption row whose name says nothing is not guessed to be
  one: a guessed caption row would put a caption conflict on every request
  that touches a picture.
