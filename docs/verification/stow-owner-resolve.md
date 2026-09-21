# Stow owner resolution verification

Audience: maintainer verification.

This record supports the opt-in `bin/fm-stow-owner-resolve.sh` contract owned by [`../configuration.md`](../configuration.md) ("Stow owner resolution") and the knowledge-routing table owned by `AGENTS.md` section 6.
It records only facts that must be re-established when the typesafe.ai model, its API, or the owner options change.
Task chronology stays in the private task report.

## The API the tool depends on

Verified 2026-09-21 against `https://ai-gateway.vercel.sh/typesafe/v1/systemone` (gateway rung, model `typesafe-ai/jev`).
`POST /v1/systemone` takes `{model, state, questions}`; a `choice` question returns `{choice, probabilities, confidence}` with the probabilities summing to 1.
Observed end-to-end latency from a Mac was 253 to 518 ms per request; input tokens were 493 to 513 and output tokens 99 to 102 per call.

## Live owner match against a hand-labeled set

Run 2026-09-21 with the home key, model `typesafe-ai/jev` on the gateway rung, confidence floor 0.6, timeout 5 s.
Findings: 12 hand-labeled items covering every owner (two each for `captain-md`, `learnings-md`, and `backlog-note`, one each for the rest) plus 2 deliberately vague items with no clear owner.

| # | Hand label | Result | Confidence | Agreement |
| --- | --- | --- | --- | --- |
| 1 | captain-md | captain-md | 0.96 | agree |
| 2 | captain-shared-md | captain-shared-md | 0.87 | agree |
| 3 | learnings-md | project-agents-md | 0.71 | disagree (debatable: the treehouse branch rule reads as contributor knowledge) |
| 4 | learnings-md | ambiguous | 0.26 | withheld (split across project and fleet readings) |
| 5 | backlog-note | backlog-note | 0.73 | agree |
| 6 | scout-report | scout-report | 0.96 | agree |
| 7 | project-agents-md | project-agents-md | 0.97 | agree |
| 8 | firstmate-tracked | firstmate-tracked | 0.87 | agree |
| 9 | elsewhere-or-drop | ambiguous | 0.25 | withheld (top probabilities tied 0.34/0.34, correctly not guessed) |
| 10 | captain-md | captain-md | 0.96 | agree |
| 11 | learnings-md | ambiguous | 0.40 | withheld (fleet-wide vs home-local reading is genuinely uncertain) |
| 12 | backlog-note | backlog-note | 0.97 | agree |
| 13 | (vague, no owner) | ambiguous | 0.21 | withheld |
| 14 | (vague, no owner) | ambiguous | 0.26 | withheld |

Of the 9 clear outcomes, 8 matched the hand label and none was a confident misroute; the one disagreement (item 3) is a genuinely debatable fleet-vs-contributor reading, not a confusion of unrelated owners.
All 5 ambiguous outcomes correctly emitted no `owner:` line, including both vague items and the tied item 9, so uncertain findings return to manual routing rather than guessing.
Item 2 re-run with `--secondmate-home` stayed `captain-shared-md` at 0.91 confidence with `write: route-to-primary`, proving gate (a) on the live path.

## Offline behavior

`tests/fm-stow-owner-resolve.test.sh` drives the public interface with a fake `curl` that records argv, the request body, the header read from file descriptor 3, and whether the secret reached its environment, plus a recording fake `quota-axi`.
It proves the absent key (environment and `.env`) prints one stderr line, nothing on stdout, exits 0, and never invokes `curl`.
It proves the request uses the fixed endpoint and model, carries only the finding text and one owner Choice with one option per knowledge owner plus the fixed neutral none option, and never carries tier, pinning, or aging language.
It proves the secondmate read-only reroute, the project-memory delivery-path gate, the fixed-floor ambiguous outcome with no owner or write lines, and the gateway-first ladder with its one-call fallback.
It proves missing-curl and quota-axi are never touched, HTTP 500, transport failure, malformed or incomplete probabilities, out-of-range confidence, and unknown owner ids are error outcomes with exit 0, and missing, unreadable, or empty learning files plus unknown flags exit 2 before any network call.

```console
$ bash tests/fm-stow-owner-resolve.test.sh | grep -c '^ok'
11
```

A live run needs a key and is not part of the suite; rerun the table above by pointing the tool at a finding file with the home key available.
