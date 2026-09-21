# Typed intake classification verification

Audience: maintainer verification.

This record supports the opt-in `bin/fm-intake-kind.sh` contract owned by [`../configuration.md`](../configuration.md) ("Typed intake classification") and the ship/scout definitions owned by `AGENTS.md` section 7.
It records only facts that must be re-established when the typesafe.ai model, its API, or the code-gate word lists change.
Task chronology stays in the private task report.

## The API the tool depends on

The tool speaks the same `POST /v1/systemone` Choice shape as `bin/fm-dispatch-resolve.sh` with a fixed two-option `kind` question; see [`dispatch-resolve.md`](dispatch-resolve.md) ("The API the tool depends on") for the verified endpoint, error shapes, and latency bands.
No separate live probe was run for this tool: the request/response mechanics are identical apart from the question builder, and the offline suite pins them with a fake `curl`.

## Offline behavior

`tests/fm-intake-kind.test.sh` drives the public interface with a fake `curl` that records argv, the request body, the header read from file descriptor 3, and whether the secret reached its environment.
It proves the absent key (environment and `.env`) prints one stderr line, nothing on stdout, exits 0, and never invokes `curl`.
It proves a `.env` key turns the tool on and the environment wins over it.
It proves the key is absent from the child environment, never appears on `curl` argv, and arrives only as the bearer header on the descriptor.
It proves the request uses the fixed endpoint and model, carries the whole intake file as the task state, and asks only the `kind` Choice with exactly the two fixed criteria and no other payload.
It proves the explicit-knowledge-request gate forces scout over a 0.95 ship answer, the diagnostic-evidence gate escalates a ship answer that only cites a report while an authorized change still clears, the existing-evidence gate keeps a clear scout with its absorb advisory, and the asymmetric floor keeps an uncertain scout decided while returning an uncertain ship to the manual checks.
It proves missing-curl and quota-independent transport failures, HTTP 500, malformed usage, zero-mass or malformed probabilities, out-of-range confidence, and unknown kind choices are error outcomes with exit 0, while a missing, unreadable, or empty intake file, an unknown flag, and `--help` behave as the contract states, with configuration errors exiting 2 before any network call.
It proves the gateway-first ladder answers on the free rung with one call, keeps typesafe-only behavior, and descends exactly once on a 429 refusal with the descended rung named.
It proves intake newlines cannot inject a second `kind:` line.

```console
$ bash tests/fm-intake-kind.test.sh | tail -1
ok - hand-labeled agreement: 18 of 18 intakes resolve to their hand label
```

The canonical check is the exit code: the suite exits non-zero on any `not ok`.

## Hand-labeled agreement (offline, 2026-09-21)

Method: the same hand-labeled method as the dispatch resolver, minus the live model.
`tests/fixtures/fm-intake-kind-labels.tsv` holds 18 hand-labeled intakes (4 clear ship, 6 clear scout, 3 evidence-held, 4 below-floor, 2 gate-override probes); the suite's final block scripts the fixture's model-plausible answer per row (deliberately wrong on the gate-override rows) and asserts status, kind, gate, and note through the public interface.

| Measure | Result |
| --- | --- |
| Intakes resolving to their hand label | 18 of 18 |
| Gate (a) overrides of a wrong ship answer | 4 of 4 forced scout |
| Gate (b) holds of unevidenced ship answers | 3 of 3 escalated |
| Below-floor scouts staying scout | 2 of 2 |
| Below-floor ships returning to manual checks | 2 of 2 |

## Open live run

A keyed live run against the same 18 intakes is still open: inject the key for one command, record agreement with the hand labels, per-call latency, and input/output tokens in the table above, following the dispatch-resolve method.
No live accuracy claim is made until that run exists.
