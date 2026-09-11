# Gemma 4 12B as a resident server (opencode + pipeline)

The local `mlx-community/gemma-4-12b-it-4bit` (6.3 GB, already in the
HuggingFace cache) serves OpenAI-compatible chat over HTTP via the
`mlx_vlm` server - no translation shim. Verified with mlx-vlm 0.6.13,
opencode 1.18.30, 2026-09-09.

## 1. Start the server

```sh
python3 -m mlx_vlm server \
    --model mlx-community/gemma-4-12b-it-4bit \
    --host 127.0.0.1 --port 8080
```

Check it:

```sh
curl -s http://127.0.0.1:8080/health          # loaded_model, context 262144
curl -s http://127.0.0.1:8080/v1/models | head -c 300
```

Use `mlx_vlm`'s server, not `mlx_lm`'s - the latter has zero
image/audio support.

## 2. opencode provider block (PROPOSED - captain applies this)

`~/.config/opencode/opencode.jsonc` is the captain's machine config; the
repo never edits it. Merge this block into the top-level object:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "mlx-vlm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Gemma 4 12B (local)",
      "options": {
        "baseURL": "http://127.0.0.1:8080/v1"
      },
      "models": {
        "mlx-community/gemma-4-12b-it-4bit": {
          "name": "Gemma 4 12B (local)"
        }
      }
    }
  }
}
```

No API key is needed - the server accepts bare requests, and opencode
1.18.30 accepts a bare `baseURL` with no key configured. Proven from a
scratch dir (captain's config untouched): `opencode models mlx-vlm`
listed the model and `opencode run --model
'mlx-vlm/mlx-community/gemma-4-12b-it-4bit' 'Reply with exactly:
OPENCODE_OK'` returned `OPENCODE_OK`.

## 3. What survives the round trip (measured, not claimed)

All four modalities verified with real `POST /v1/chat/completions`
requests against the server above:

| Media | Content part | Result |
|---|---|---|
| text | `{"type":"text"}` | Works (`TEXT_OK` echo, ~2.6 s) |
| image | `{"type":"image_url","image_url":{"url":...}}` | Works - base64 data URI and plain local path both reach the model (293 vision prompt tokens; ~3.9 s) |
| video | `{"type":"video_url","video_url":{"url":...}}` | Works - base64 data URI and plain local path both reach the model (312 prompt tokens, colour question answered correctly; ~4.3 s) |
| audio | `{"type":"input_audio","input_audio":{"data":...,"format":"wav"}}` | Works - raw base64 and `data:audio/wav;base64,` both transcribe 6 s of real speech word-perfect (~4-5 s) |

Two honest caveats:

- A pure 440 Hz sine tone came back "I cannot hear any audio" even
  though the server log shows `audio=1` for the request - the payload
  arrives, but the model dismisses content-free tones. Speech
  transcribes exactly. Judge audio on speech, not tones.
- `opencode run -f <binary>` does NOT deliver media parts: the harness
  inlines attachments as text (server log shows `images=0` on those
  requests). Text chat through opencode is proven; media parts are
  proven at the endpoint with the exact `image_url` shape the AI SDK's
  `openai-compatible` provider emits. TUI image-paste was not verified
  headlessly.

## 4. Pipeline use (`library/tools/vision_model.py`)

`analyze_image` / `analyze_images` / `analyze_video` try the server
first (plain local paths - no base64 bloat; server and pipeline share a
machine) and fall back to the existing in-process load otherwise. The
in-process path is intact and still the only path when the server is
down; a pipeline that needs a running server to work at all was
explicitly rejected.

Both directions announce on stderr (stdout stays JSON-clean):

- server answers: `GEMMA SERVER answered <method> at <url> (resident,
  no model load).`
- server unreachable (connection error, timeout, HTTP 5xx): `GEMMA
  SERVER unreachable at <url> (...) ; falling back to in-process
  <model> (full model load, ~6s, ~7GB resident).`
- HTTP 4xx propagates instead of falling back - a malformed payload is
  our bug, and a slow in-process run would hide it.

Knobs:

| Env | Default | Effect |
|---|---|---|
| `GEMMA_SERVER_URL` | `http://127.0.0.1:8080` | Server base URL; empty string disables the server path |
| `GEMMA_SERVER_MODEL` | `mlx-community/gemma-4-12b-it-4bit` | Model id sent in chat requests |
| `GEMMA_SERVER_TIMEOUT` | `300` | Seconds per HTTP request |

Tests: `tests/test_vision_model_server.py` (mocked server; no model
load). Live proof on 2026-09-09: `analyze_image` + `analyze_video`
through the module against the running server, both answered over the
server path.

## 5. The cost (measured 2026-09-09, 24 GB MacBook)

- Resident server, model loaded, idle: **~7.3 GB** process (`top` MEM
  7279M; MLX `peak_memory_gb` 6.87-7.41 across requests).
- Under a request: same band - **7.3-7.9 GB** (7895M observed after
  cache-clear + reload). Each request itself runs ~2.5-5 s.
- Machine showed real pressure with the server up under normal load
  (~120 MB free pages at one sampling).
- `POST /unload` clears caches (`loaded_model: null`) but frees only
  **~145 MB** (7279M -> 7134M) - it is a cache reset, not a memory
  release. The next request reloads transparently and answers. Only
  stopping the process frees the ~7 GB.
- Effect on a concurrent Resolve render: **not measured** - no
  headless render harness exists in this environment. The arithmetic
  the captain decides on: ~30% of a 24 GB machine held resident for as
  long as the server runs, on the project's render bottleneck.

Always-on versus start-on-demand is the captain's call. The supported
on-demand shape is §6: the shim below, not manual start/stop.

## 6. On-demand: the shim, the `gemma` command, and the pipeline scope

`library/tools/gemma_shim.py` (stdlib only) answers the FIXED provider
URL on :8080 with a ~40 MB shim. The real backend runs on 127.0.0.1:8081
(`GEMMA_BACKEND_PORT`) and exists only while needed:

- first model request -> shim spawns the backend, waits for `/health`
  (bounded by `GEMMA_STARTUP_TIMEOUT`, default 120 s), then
  reverse-proxies - streaming (SSE) included, so opencode chat works;
- no in-flight requests, no holds, and no traffic for `GEMMA_IDLE_TIMEOUT`
  (default 210 s - the captain's 3.5 minutes, 2026-09-11; do not raise it
  or "tidy" it back to 600) -> the backend PROCESS is stopped, returning
  the ~7 GB. Idle means all three: a teardown can never kill live work;
- past that, with the backend stopped and still no traffic for
  `GEMMA_SHIM_IDLE_EXIT` (default 60 s - the shim holds ~40 MB against
  the backend's ~7 GB, and a cold shim start is ~0.5 s, so a short delay
  smooths bursts without ever reading as permanent) -> the shim exits
  ITSELF, returning the port to launchd. End state: nothing resident.
  The shim may only exit once the backend is stopped - exiting while the
  backend lives would orphan a 7 GB process with nothing to reap it
  (asserted in code, proven by
  `test_shim_exits_only_after_backend_stopped`);
- backend fails to start -> HTTP 503 with a JSON `error.message` naming
  the cause and the fix (`gemma up --prewarm`, `gemma status`). A failure
  is a clear message, never a hang or an empty reply;
- `GET /health` on a cold shim answers `{"status":"idle",...}` WITHOUT
  loading the model; once warm it proxies the backend's `/health`
  verbatim, so the §1 checks keep working.

```
gemma up [--prewarm]   ensure the shim is listening (provider URL unchanged)
gemma down [--all]     stop the backend now (--all also stops the shim;
                       refused with 409 while requests or holds are live)
gemma status           shim + backend state and the backend's resident RSS
gemma install-agent    hand :8080 to launchd (socket activation: the shim
                       starts per connection and exits when idle - nothing
                       resident; machine paths are generated at install
                       time, never committed)
gemma uninstall-agent  bootout, delete the plist, clear shim state: leaves
                       nothing behind
```

`scripts/gemma` is the same entry point (`python3 -m
library.tools.gemma_shim ...`). Pipeline bursts hold the backend across
gaps longer than the idle timeout instead of racing the reaper:

```python
from library.tools.gemma_shim import server_scope
with server_scope("http://127.0.0.1:8080"):
    ...  # vision batch; backend cannot be reaped inside
```

### 6.1 Why this shape (options evaluated 2026-09-09)

Cold start measured that day, model already in the HF cache: ~12 s
process spawn to `/health` healthy, ~4 s more to the first chat answer
(~13 s end-to-end through the shim on a second run). That number sets the
210 s idle default (captain, 2026-09-11: 3.5 minutes): a 60 s timeout
would bill the captain ~13 s after every minute idle; 210 s holds the 7 GB
only across a working session, and every proxied request resets the clock.

- **Shim (chosen).** Fixed URL keeps working for both consumers, memory
  is held only while in use. Cost: the first request after idle pays the
  ~13 s load. Live proof: cold `POST /v1/chat/completions` answered
  `SHIM_COLD_OK`; backend `top` MEM 7578M while serving; reaped after the
  idle window (free pages 2850 -> 69017) with the shim still answering.
- **launchd socket activation for the shim (adopted 2026-09-11, captain's
  call: nothing resident).** The `mlx_vlm server --help` objection in the
  earlier revision was aimed at the wrong layer: the BACKEND takes no
  inherited fd, but the SHIM is our code, so the shim takes launchd's
  listener via `launch_activate_socket` (ctypes, libSystem - `launch_msg`
  is deprecated and unused). launchd holds :8080, starts the shim per
  connection, and the shim exits itself past backend-idle + shim-exit.
  `gemma install-agent` generates the plist at install time (no machine
  paths committed); without launchd the shim binds its own port so
  `gemma up` still debugs.
- **opencode plugin (rejected).** The installed `@opencode-ai/plugin`
  1.18.29 exposes `chat.message`, `tool.execute.before/after`,
  `command.execute.before`, `shell.env`, session-compacting hooks - NO
  session-start/end lifecycle, so a plugin can neither warm the model
  before first use nor tear it down after, and it does nothing for the
  pipeline. The shim covers chat with zero opencode config.
- **Short command alone (adopted as the primitive).** `gemma up/down/
  status` is honest and the right layer UNDER the shim, but manual-only
  does not answer "automatically when needed".
- **`POST /unload` (rejected, again).** Frees ~145 MB; the process keeps
  the ~7 GB. Nothing here calls it.

Known unknown, still open: whether repeated load/unload cycles leak
memory on this stack (two cold starts measured at 7502M / 7578M - same
band, but that is two samples, not a soak test).

### 6.2 Captain's machine (INSTALLED - `gemma install-agent`)

The repo never writes here; the command below does, and only when the
captain runs it. Machine paths are generated at install time - no plist
is committed.

```sh
cd <this-repo> && ./scripts/gemma install-agent
```

This writes `~/Library/LaunchAgents/dev.video-editing-pilot.gemma-shim.plist`
with a `Sockets/Listeners` entry on 127.0.0.1:8080 (NO `KeepAlive`, NO
`RunAtLoad` - launchd holds the port and starts the shim per connection,
then re-arms when the shim exits itself) and bootstraps it. Current
`GEMMA_IDLE_TIMEOUT` / `GEMMA_SHIM_IDLE_EXIT` / `GEMMA_BACKEND_PORT` /
`GEMMA_MODEL` values are baked into the plist's `EnvironmentVariables`,
so re-run install after changing them. Removal is symmetric:

```sh
./scripts/gemma install-agent    # hand the port to launchd
./scripts/gemma uninstall-agent  # bootout + delete plist + clear state
```

The old start-at-login proposal (KeepAlive shim, 40 MB always resident)
is withdrawn: the captain explicitly asked for nothing permanent.

Tests: `tests/test_gemma_shim.py` (fake stdlib backend; no model load)
covers cold-proxy, idle reap, in-flight protection, hold/release scope,
503-on-failure, and 409-on-busy-stop.
