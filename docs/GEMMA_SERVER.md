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
on-demand shape: start the server before pipeline steps that need
vision (`ask_the_footage`, perceptual QA), stop it before a render.
