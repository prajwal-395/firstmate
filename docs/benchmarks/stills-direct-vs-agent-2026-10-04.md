# Direct still calls versus serialized agent handoffs

Date: 2026-10-04
Branch: fm/vep-stills-direct-vs-agent
Benchmark source: read-only copy at /private/tmp/vep-benchmark-current-head.q3obr_9g/001
Request set: the 10 still-inspection requests documented in docs/benchmarks/stills-gemma-vs-cloud-2026-10-04.md

## Result

Direct Codex CLI calls through the OAuth subscription completed as a concurrent batch in 20.455 seconds. The current still handshake, answered by a Codex agent one request at a time, took 352.041 seconds across its 10 requests. On this run, batching reduced elapsed time by 94.2% (17.2x).

Reported token usage was not equal. The serialized agent route used 1,581,156 input tokens, including 1,192,448 cached input tokens, and 11,929 output tokens. The direct route used 475,884 input tokens, including 169,216 cached input tokens, and 5,090 output tokens. Cached input is a subset of input, not an additional amount. The CLI does not expose image-token counts or per-call subscription cost, so this cannot establish equal or lower dollar cost. Its input totals include image processing, but image tokens cannot be separated from other input tokens.

The image review found mixed answer quality. Direct calls were more accurate on some storefront lettering and some appearance timing. The handoff route supplied more context on a couple of detail requests. Both routes made unsupported text claims and timing errors. This small qualitative sample has no overall quality winner.

## Method

I used the same 10 coarse and detail requests, the same prompt text and known-entities context, and the same model setting on both routes: gpt-6-luna with low reasoning effort. Both routes used Codex CLI sessions authenticated through the existing OAuth subscription; no API key or API billing was used.

For the agent route, I created scratch project copies and invoked the existing still-vision host-answer handshake for each request. Each request was answered by a separate headless Codex session in series. The session read the handshake request and its attached images, then wrote the answer to the exact response path expected by the handshake. This includes the agent's request-reading and response-writing instructions, CLI startup, tool use, and handshake wait.

For the direct route, I launched 10 headless Codex CLI calls concurrently. Each received the exact request prompt and its stills as image attachments. Batch wall time is measured from launch until all 10 calls completed. The reported per-request wall times are each CLI call's elapsed time; their sum is 173.986 seconds because calls overlapped. The agent route's requests did not overlap, so its summed per-request wall time is also its batch elapsed time.

The benchmark copy did not contain the original October 3 still-cache JPEGs. I reconstructed 84 stills from the same source videos at the recorded request timestamps, using the pipeline's ffmpeg extraction behavior. Prompt text and known-entities strings came from the prior benchmark document. For IMG_1820 detail, the requested endpoint is 52.550 seconds, beyond the last decodable frame at 52.503333 seconds; I used that last frame, 46.7 milliseconds earlier. Therefore the images match the source clips and sampled times but are not byte-identical to the original cached files.

I reviewed contact sheets for all 10 requests. The quality notes below are qualitative visual judgments, not a blinded or scored evaluation.

## Timing

| Request | Images | Agent serial wall | Direct call wall |
|---|---:|---:|---:|
| IMG_1808 coarse | 7 | 45.818s | 20.453s |
| IMG_1808 detail | 2 | 24.350s | 11.544s |
| IMG_1812 coarse | 15 | 31.022s | 18.769s |
| IMG_1812 detail | 2 | 21.344s | 11.779s |
| IMG_1816 coarse | 15 | 37.200s | 19.612s |
| IMG_1816 detail | 4 | 28.067s | 16.807s |
| IMG_1818 coarse | 15 | 29.907s | 20.218s |
| IMG_1818 detail | 6 | 37.911s | 15.831s |
| IMG_1820 coarse | 12 | 39.472s | 19.092s |
| IMG_1820 detail | 6 | 56.950s | 19.881s |
| **Batch** | **84** | **352.041s serial** | **20.455s at concurrency 10** |
| **Per-call median** | | **34.111s** | **18.931s** |

## Token and tool usage

Usage was read from each Codex JSONL event stream's completed-turn usage record. Input, cached input, output, and reasoning output are the fields reported by the CLI. Cached input is included in the input total. Reasoning output was zero for every measured request. Image tokens are not reported separately. Tool calls are completed command-execution or file-change items in the CLI event stream; they are counted here to make route overhead visible.

Each usage tuple below is input / cached input / output / tool calls.

| Request | Agent usage | Direct usage |
|---|---:|---:|
| IMG_1808 coarse | 249,834 / 211,456 / 1,726 / 5 | 40,377 / 7,936 / 675 / 0 |
| IMG_1808 detail | 84,629 / 61,696 / 820 / 2 | 27,679 / 7,936 / 255 / 0 |
| IMG_1812 coarse | 183,672 / 126,848 / 1,100 / 2 | 60,283 / 18,176 / 607 / 0 |
| IMG_1812 detail | 84,819 / 61,696 / 585 / 2 | 27,735 / 12,032 / 252 / 0 |
| IMG_1816 coarse | 183,993 / 130,944 / 1,436 / 2 | 60,361 / 22,272 / 575 / 0 |
| IMG_1816 detail | 99,873 / 71,808 / 979 / 2 | 32,755 / 7,936 / 536 / 0 |
| IMG_1818 coarse | 183,046 / 130,944 / 850 / 2 | 60,282 / 12,032 / 596 / 0 |
| IMG_1818 detail | 194,272 / 160,000 / 1,383 / 4 | 37,703 / 7,936 / 337 / 0 |
| IMG_1820 coarse | 161,356 / 112,128 / 1,430 / 2 | 52,786 / 18,176 / 645 / 0 |
| IMG_1820 detail | 155,662 / 124,928 / 1,620 / 3 | 75,923 / 54,784 / 612 / 1 |
| **Total** | **1,581,156 / 1,192,448 / 11,929 / 26** | **475,884 / 169,216 / 5,090 / 1** |

The agent route reported 388,708 non-cached input tokens (input minus cached input); direct reported 306,668. Agent input tokens were 3.32x direct input tokens, while non-cached input was 1.27x and output was 2.34x. The agent totals include its handshake instructions, request/response file handling, and tools. The direct CLI's request 10 made one command-execution tool call to inspect the scratch environment; the other nine direct calls made no tool calls. These are subscription token counters, not a cost meter. No dollar estimate is available from this login.

## Visual comparison

| Request | What the stills show and how the answers compared |
|---|---|
| IMG_1808 coarse | Both found the man and car cabin. Direct named more distinct interior details, while the agent included the bag and warning labels. Both split the main subject's appearance around the frames where he is cropped. |
| IMG_1808 detail | The images show the man leaning in the cabin, passenger-seat shoes, a hanging device, and exterior through the open door/window area. The agent gave more of that contextual detail; the direct answer was much sparser. |
| IMG_1812 coarse | Both found the subject, backpack, and parking-garage facade. Direct described more of the surrounding scene. Both were cautious or unclear about the red mark on the pillar. |
| IMG_1812 detail | Direct's shorter appearance spans better matched the change from the subject to the handrail and street view. The agent used endpoint-only timing that missed this shift. |
| IMG_1816 coarse | Broad scene coverage was similar. Direct was more specific about the kiosk and signs while expressing uncertainty about small lettering. |
| IMG_1816 detail | Both captured the central plaza scene. The agent read a vertical sign as “MARKET”; the still does not support that confidently. Direct's statement that the lettering was unclear was better calibrated. |
| IMG_1818 coarse | Direct read the mirrored GFitPro storefront sign and THE LOCAL lettering. The agent only approximated GFitPro, so direct was stronger on the visible text in this request. |
| IMG_1818 detail | Both described the street and parked cars. The agent added distant sign and pedestrian details, but those small elements are uncertain; neither gave a consistently grounded refinement of the interval. |
| IMG_1820 coarse | Both found the green Chattahoochee Row sign but misread the mirrored storefront lettering. Direct gave more scene detail; neither text answer is reliable here. |
| IMG_1820 detail | The agent tracked the garage scene more continuously. Direct split some appearances inaccurately. Both overclaimed the mirrored wall lettering with variants of “...BREWING CO.” |

## Measurement exclusions and limits

These were setup attempts, not part of the valid 10-request cohorts:

- A direct single-call probe for IMG_1808 coarse used 40,374 input tokens (7,936 cached) and 415 output tokens. It was excluded because the final direct measurement used a ten-way concurrent batch.
- A failed agent attempt on IMG_1820 detail used 235,280 input tokens (199,168 cached) and 1,884 output tokens before a partial response file was read. I reran that request with an atomic response write; only the successful rerun is in the agent cohort above.
- An initial agent IMG_1808 coarse harness attempt used the wrong response path and was stopped by its exact runner PID. It had no persisted usage event, so its token use is unknown and excluded.

This experiment changes no Ren route code and used no Resolve. Its timing is end-to-end CLI and handshake latency, not isolated model inference latency. Subscription billing is opaque to the CLI, and image token counts are unavailable, so the token report cannot prove dollar-cost parity. The reconstructed images and small qualitative sample also limit how strongly to generalize the quality comparison.
