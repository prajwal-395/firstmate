# Drain triage verification

Audience: maintainer verification.

This record supports the opt-in `bin/fm-drain-triage.sh` contract (the script header owns its exact flags and output lines) and the observe-only counts hook in `bin/fm-wake-drain.sh` (that script owns the hook mechanics).
It records only facts that must be re-established when the typesafe.ai model, its API, or the wake-row shapes change.
Task chronology stays in the private task report.

## The API the tool depends on

The tool speaks the same `POST /v1/systemone` typed-question shape `bin/fm-dispatch-resolve.sh` uses, verified against `https://api.typesafe.ai` in `dispatch-resolve.md`.
It sends one call per drain with one `choice` question per classifiable row (`suppress` or `wake`) and validates one typed answer per asked row id.
Heartbeat rows never reach the model; they wake in code.

## Offline proof with recorded wake batches

Run 2026-09-21 with a stubbed `curl` (no network) and canned per-batch Jev answers, model `jev-latest`, confidence floor 0.6, timeout 5 s.
Batches and their closing-marker states persist under `docs/verification/drain-triage/` so the run is reproducible.
The canned answers stand in for model judgement; what this run proves is the plumbing (row parsing, evidence gathering, floor enforcement, counts arithmetic), not model quality, which is what the live observation week measures.

```console
$ export PATH="/tmp/dt-proof/bin:$PATH" TYPESAFE_API_KEY=proof-dummy
$ FAKE_CURL_RESPONSE=/tmp/dt-proof/resp-01.json ./bin/fm-drain-triage.sh --state docs/verification/drain-triage/batch-01-state --rows-file docs/verification/drain-triage/batch-01-closed-signals.tsv
$ FAKE_CURL_RESPONSE=/tmp/dt-proof/resp-02.json ./bin/fm-drain-triage.sh --state docs/verification/drain-triage/batch-02-state --rows-file docs/verification/drain-triage/batch-02-mixed.tsv
$ ./bin/fm-drain-triage.sh --state docs/verification/drain-triage/batch-02-state --rows-file docs/verification/drain-triage/batch-03-heartbeat-only.tsv
```

| Batch | Shape | Result |
| --- | --- | --- |
| `batch-01-closed-signals.tsv` | Three signal rows whose status files already record terminal outcomes (the measured 2026-09-21 no-op shape) | `status: clear`, `counts: suppressed=3 actionable=0 rows=3` |
| `batch-02-mixed.tsv` | Open signal, heartbeat, check, stale; the check answer arrives at 0.55 confidence | `status: ambiguous`, `counts: suppressed=0 actionable=4 rows=4`, the below-floor row wakes with its floor note, the heartbeat wakes in code |
| `batch-03-heartbeat-only.tsv` | Heartbeat only | `status: clear`, `counts: suppressed=0 actionable=1 rows=1`, no model call at all |

## Offline behavior

`tests/fm-drain-triage.test.sh` drives the public interface with a fake `curl` that records argv, the request body, the header read from file descriptor 3, and whether the secret reached its environment.
It proves the absent key (environment and `.env`) prints one stderr line, nothing on stdout, exits 0, and never invokes `curl`.
It proves a `.env` key turns the tool on and the environment wins over it.
It proves the request uses the fixed endpoint, model, and five-second timeout, carries one Choice question per queued row with exactly the `suppress` and `wake` options, keeps the key off argv and out of child environments, and carries each row's closing-marker evidence in its own instructions.
It proves closed rows can suppress while open rows wake, below-floor rows wake as today with the batch marked ambiguous, and error outcomes (HTTP 500, malformed answers, unasked answers) exit 0 with every row counted actionable.
It proves heartbeat rows always wake in code without a model call, overflow past the row cap wakes unasked in code, the gateway-first ladder falls back once on refusal, and usage or configuration errors exit 2 before any network call.

```console
$ bash tests/fm-drain-triage.test.sh | tail -1
# all fm-drain-triage tests passed
```

## Live observation plan

The drain hook in `bin/fm-wake-drain.sh` appends each opted-in drain's printed block to `state/.drain-triage-counts.log` (1 MB cap, newest 2000 lines kept) and never changes presented rows, acknowledgement, or exit status.
Homes without either key skip the hook with no spawn and no log line, so existing behavior is byte-identical there.
After one week of counts, compare the tool's would-suppress rows against what the supervisor turns actually did; only widen toward real suppression when the log shows the classifier agrees with reality.
A live keyed run also re-validates the multi-question request shape against the real API before any widening.
