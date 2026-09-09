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
  (default 600 s) -> the backend PROCESS is stopped, returning the ~7 GB.
  Idle means all three: a teardown can never kill live work;
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
600 s idle default: a 60 s timeout would bill the captain ~13 s after
every minute idle; 600 s holds the 7 GB only across a working session,
and every proxied request resets the clock.

- **Shim (chosen).** Fixed URL keeps working for both consumers, memory
  is held only while in use. Cost: the first request after idle pays the
  ~13 s load. Live proof: cold `POST /v1/chat/completions` answered
  `SHIM_COLD_OK`; backend `top` MEM 7578M while serving; reaped after the
  idle window (free pages 2850 -> 69017) with the shim still answering.
- **launchd socket activation (rejected).** `mlx_vlm server --help`
  offers only `--host/--port` - no inherited-fd option, so there is no
  socket to activate. KeepAlive without it is just always-on under
  another name. launchd remains the right owner for the *shim* - see the
  proposed plist in §6.2.
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

### 6.2 Captain's machine (PROPOSED - captain applies this)

The repo never writes here. Two steps, both opt-in:

1. Start the shim (survives the terminal; the provider URL answers
   from here on):

   ```sh
   cd <this-repo> && ./scripts/gemma up
   ```

2. Keep the *shim* (not the model) across reboots - `~/Library/
   LaunchAgents/dev.video-editing-pilot.gemma-shim.plist` with EXACTLY
   this content (`<this-repo>` replaced with the checkout path):

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
     "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
     <dict>
       <key>Label</key>
       <string>dev.video-editing-pilot.gemma-shim</string>
       <key>ProgramArguments</key>
       <array>
         <string>/opt/homebrew/bin/python3</string>
         <string>-m</string>
         <string>library.tools.gemma_shim</string>
         <string>run</string>
       </array>
       <key>WorkingDirectory</key>
       <string>&lt;this-repo&gt;</string>
       <key>RunAtLoad</key>
       <true/>
       <key>KeepAlive</key>
       <true/>
       <key>StandardOutPath</key>
       <string>/tmp/gemma-shim.stdout.log</string>
       <key>StandardErrorPath</key>
       <string>/tmp/gemma-shim.stderr.log</string>
     </dict>
   </plist>
   ```

   Apply with:

   ```sh
   launchctl load -w ~/Library/LaunchAgents/dev.video-editing-pilot.gemma-shim.plist
   ```

Tests: `tests/test_gemma_shim.py` (fake stdlib backend; no model load)
covers cold-proxy, idle reap, in-flight protection, hold/release scope,
503-on-failure, and 409-on-busy-stop.
