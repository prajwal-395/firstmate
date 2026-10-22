# Typed dispatch resolution verification

Audience: maintainer verification.

This record supports the deterministic `bin/fm-dispatch-resolve.sh` contract owned by [`../configuration.md`](../configuration.md) ("Typed dispatch resolution") and the declared rule and profile fields owned there under "Crew dispatch profiles".
It records only facts that must be re-established when firstmate's dispatch rules change.
Task chronology, the captain's rules, and the briefs themselves stay in the private scout report.

## Retired judgment routing

Model-judgment dispatch routing was removed on 2026-10-22 by the dispatch-policy migration: the resolver performs no network judgment call and reads no judgment key.
The typesafe.ai API notes and live rule-match tables below are historical evidence for that removed path, kept for provenance and never re-established.

## The API the removed path depended on (historical)

Verified 2026-09-16 against `https://api.typesafe.ai`.
`GET /v1/models` listed `jev-latest` and `jev-preview`, both released 2026-09-10; a `jev-latest` request answered as `jev-1.13.0`.
`POST /v1/systemone` takes `{model, state, questions}`; a `choice` question returns `{choice, probabilities, confidence}` with the probabilities summing to 1.
Observed error shapes: 401 `authentication_error` for a bad key, 403 when the header is missing, 422 with a `detail[].loc` naming the offending field, 400 `api_usage_error` for an unknown model, 405 on GET.
No rate-limit headers were present on any response; every response carried `x-typesafe-request-id`.
Observed end-to-end latency from a Mac was 123 to 348 ms per request, with the server's own upstream time at 4 to 60 ms.

## Live rule match against real briefs (historical)

Run 2026-09-16 with the key injected for the one command through the vault (`av inject +TYPESAFE_API_KEY -- ...`), model `jev-latest`, confidence floor 0.6, timeout 5 s, one `quota-axi --json` snapshot for the whole run.
Rules: the captain's five-rule file with a captain-authored none option, one `approval: captain` rule, two rule floors on `model:fable`, and declared `provider` on the Pi profiles.
Briefs: 15 real briefs from this home's recent work plus 10 synthetic ones written to hit each rule.

| Measure | Result |
| --- | --- |
| Rule matched the hand label | 20 of 25 |
| Resolved to the hand-labeled profile | 20 of 25 |
| Outcomes: clear / ambiguous / escalate / error | 18 / 1 / 6 / 0 |
| Clear results with a wrong profile | 0 |
| API latency (min / median / max) | 152 / 214 / 348 ms |
| Wall time per call including jq (min / median / max) | 198 / 261 / 396 ms |
| Input tokens per brief (min / median / max) | 1,279 / 3,114 / 4,538 |
| Output tokens | 150 to 152 |
| API errors | 0 |

Of the five disagreements, one was a wrong hand label (the brief quoted the bug-fix rule's wording verbatim), three were real briefs the model read as the approval-gated design rule at 0.66 to 0.86 confidence and escalated by design, each of which the captain had in fact dispatched at the strongest-reasoning class, and one was a synthetic tweak that came back ambiguous at 0.41 confidence and was handed back to firstmate.
A lean request that asks only the rule Choice matched the full request (rule, profile, and status) on all 25 briefs, which is why the shipped tool asks one question and keeps every gate in code.
That table records the 2026-09-16 run with the captain-authored none option.
A second live run on 2026-09-17 used the same 25 briefs, held one quota snapshot constant through a fake `quota-axi`, and exercised a copy of this branch with the shipped neutral `No listed rule applies to this task.` option and option-free interface.

| Measure | Result |
| --- | --- |
| Rule matched the hand label | 20 of 25 |
| Resolved to the hand-labeled profile | 18 of 25 |
| Outcomes: clear / ambiguous / escalate / error | 17 / 2 / 6 / 0 |
| Clear results with a profile other than the hand label | 1 |
| API latency (min / median / max) | 137 / 220 / 1,795 ms |
| Input tokens per brief (min / median / max) | 754 / 2,589 / 4,013 |
| Output tokens | 60 to 62 |
| API errors | 0 |

The maximum latency was one outlier; the next slowest request was 309 ms.
The differing clear result was a synthetic small tweak that matched the simple-bug-fix rule at 0.90 and selected `cursor-grok-4.6-medium` instead of the hand-labeled `cursor-grok-4.6-high`: the tweak exemption removed from the none-option text belongs in that rule's own `when` text.
Two default-labeled briefs became ambiguous.

## Task-section payload comparison (historical)

Run 2026-09-21 with the home key, model `jev-latest`, confidence floor 0.6, and one `quota-axi --json` snapshot for the whole run.
Rules: the captain's live two-rule file (genuinely unshaped work, Lucie work) with the opencode free-first default and no approval rule.
Briefs: 14 recent real briefs from this home plus 4 synthetic edge cases (unshaped work, Lucie work, defined firstmate-repo work, approval-gated design), each hand-labeled against the live rules and each run three times against the unmodified tool (full file, Task section, and Captain's-intent-only as temp files).

| Payload | Rule agreement with full | Hand-label accuracy | Input tokens over all 54 runs |
| --- | --- | --- | --- |
| Full file | 18 of 18 | 17 of 18 | 59,710 |
| Task section | 18 of 18 | 17 of 18 | 25,744 |
| Captain's intent only | 17 of 18 | 18 of 18 on rule | 15,894 |

The Task section averaged 29 percent of each file's bytes and matched the full file on rule, status, and outcome for all 18 briefs, which is why the shipped tool sends the project name plus the Task section.
Captain's-intent-only matched the hand label on the rule for all 18 but changed one outcome from clear to ambiguous (0.42 against the 0.6 floor) and reported lower confidence on three further briefs, so the spec subsection carries real match signal rather than only routing-hint noise.
The one brief the full file missed is genuinely ambiguous work the hand label itself flagged, and the one rule where intent-only differed from full is that same brief.
Shipped 2026-09-21: project name plus Task section, with whole-brief fallback when a brief carries no Task section.

## Offline behavior

`tests/fm-dispatch-resolve.test.sh` drives the deterministic interface with a fake `quota-axi` and a fake `curl` that fails any call, proving resolution never touches the network.
It proves an exact registered-project trigger resolves before the declaration trigger, the declaration trigger routes unshaped work without judgment, and a rules-less policy resolves the deterministic default with the Codex Plus rung first.
It proves a retired model id in the selected route is refused before spawn with its recorded replacement named, and a policy declaring the retired exhausted-ladder fallback is rejected as malformed.
`tests/fm-bootstrap.test.sh` proves bootstrap validates the deterministic policy shape, id and trigger selectors, and verified harness/effort pairs without any key.

```console
$ bash tests/fm-dispatch-resolve.test.sh | tail -1
# all fm-dispatch-resolve tests passed
```

A live judgment run needs a key and no longer exercises dispatch; the retired tables above are not rerun.
