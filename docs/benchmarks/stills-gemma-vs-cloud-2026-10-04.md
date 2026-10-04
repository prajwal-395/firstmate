# Gemma versus host route for still-frame inspection

Date: 2026-10-04
Benchmark reference: run `20261003T152705-40063`, read-only copy `/private/tmp/vep-benchmark-current-head.q3obr_9g/001`
Branch: `fm/vep-stills-gemma-vs-cloud`

## Verdict

**Not the same quality on this 10-request sample.** Gemma often finds the broad scene and was strongest on the IMG_1816 detail request. However, it omits most of the requested detail in IMG_1808 and IMG_1812, reads a parking restriction sign as `SPEED LIMIT 20`, returns repeated garbled street-sign entities for IMG_1820 coarse, and carries coarse entities into detail answers without grounding them in the requested frames. The host route also makes a serious text error on IMG_1820 coarse (`STONE BREWING CO` instead of the mirrored `WE ARE TENDER SPIRITS`) and misses the readable `Credit` sign in IMG_1818 coarse. The combined evidence does not support switching all still inspection to Gemma on the brief's quality gate.

## Method and limits

Compared 10 object-inspection requests across five clips, 84 input stills total: one coarse and one detail request per clip. The coarse sets use the benchmark's original sampled timestamps; detail sets use the original intervals and sampled detail-frame files. Both routes received the same exact prompt string and the same image paths per request. For detail requests, the `Known entities` text was the host coarse answer used by the original handoff, and was passed unchanged to both routes.

The host route was invoked through the existing `inspect_stills(..., harness='agent')` handoff. The original raw host answers were not recoverable from the benchmark ledger, so each host response below was regenerated for the same request by visually inspecting the requested stills and returning the response through that handoff. Route metadata is `host:agent`, model version `unknown`; these are regenerated answers, not the missing raw answers from the October 3 run. The judgments below independently compare both outputs with the actual stills.

Gemma used `answer_via_gemma` with the same prompts, images, and token limits, under the existing `_semantic_model_memory_reservation()` heavy-work lock and the source inference admission. The Gemma server at `127.0.0.1:8080` was unavailable, so the existing source fallback loaded `mlx-community/gemma-4-12b-it-4bit` in process. The result records this fallback on every request.

`Historical host wall` below is the benchmark's recorded wall time for the matching request ID in run `20261003T152705-40063`. `Host replay wall` is the new handoff elapsed time, which includes the interactive wait/response and is not a model-only latency. Gemma wall is the direct measured local call for this comparison. Therefore the timing comparison uses the historical host measurement against current Gemma timings, not a simultaneous same-run A/B. The historical host median is 83.645s; Gemma median is 31.184s (2.68x lower by these measurements). Historical host total is 982.804s, Gemma total is 307.809s. These are directional measurements, not an isolated cloud inference benchmark.

## Request and timing index

| # | Request | Input frames | Historical host wall | Host replay wall | Gemma wall |
|---:|---|---:|---:|---:|---:|
| 1 | `IMG_1808 coarse` | 7 | 92.672s | 110.839s | 32.819s |
| 2 | `IMG_1808 detail` | 2 | 198.870s | 64.693s | 9.615s |
| 3 | `IMG_1812 coarse` | 15 | 76.624s | 60.707s | 28.068s |
| 4 | `IMG_1812 detail` | 2 | 62.689s | 20.570s | 7.223s |
| 5 | `IMG_1816 coarse` | 15 | 148.746s | 60.663s | 38.785s |
| 6 | `IMG_1816 detail` | 4 | 78.641s | 50.666s | 28.839s |
| 7 | `IMG_1818 coarse` | 15 | 66.622s | 50.651s | 41.431s |
| 8 | `IMG_1818 detail` | 6 | 90.650s | 52.665s | 35.904s |
| 9 | `IMG_1820 coarse` | 12 | 80.646s | 70.707s | 55.577s |
| 10 | `IMG_1820 detail` | 6 | 86.644s | 134.967s | 29.548s |

## Image-grounded judgments

### 1. `IMG_1808 coarse` - 7 frames, [0, 5, 10, 15, 20, 25, 27.672]

Both find the subject and car cabin. Gemma also reads the ceiling WARNING sticker, which is visible in the first coarse frame and omitted by the host. But Gemma calls the suspended black device a dashcam mounted on the rearview mirror; the images show a hanging rectangular device beside the passenger headrest with a red button. It misses the pale tag, seat belt, and exterior wall details. The mistaken device identity is a downstream object-profile error.

### 2. `IMG_1808 detail` - 2 frames, 0.0-2.0s detail interval

The frames show the man fastening his seat belt, the hanging black device and pale tag, and light shoes on the passenger seat. Host names those distinct objects. Gemma returns only the subject and cabin, omitting all three salient changes and the background wall. That leaves the detail request with no refinement beyond the coarse profile.

### 3. `IMG_1812 coarse` - 15 frames, [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70]

Both identify the subject, backpack, facade, and trees. Host also captures the separate red-brick building, a white sedan, and the red mark on the concrete pillar without claiming readable wording. Gemma omits those entities and asserts the pillar sign reads RPSV; that text is not legible in the still. Its broader facade description is useful, but the unsupported OCR and missed vehicle/storefront alter an object profile and text inventory.

### 4. `IMG_1812 detail` - 2 frames, 0.0-2.0s detail interval

Both correctly identify the subject. The two frames also clearly show the triangular parking facade and tree branches behind him, which Host records and Gemma omits. This is a smaller contextual omission than the coarse result, but it adds no detail to the existing object profile.

### 5. `IMG_1816 coarse` - 15 frames, [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70]

Gemma finds most of the scene: the subject, two vehicles, brick and modern buildings, kiosk, walkway, and another person. Host gives better identifying detail (the pink pendant, rings, bracelets, spare tire, and kiosk screen). Gemma duplicates the modern building as a separate 'large gray building' and stretches the distant pedestrian's appearance through 70 seconds, although the sampled stills show the person briefly near the entrance. Broad scene coverage is close, but specifics and timing are weaker.

### 6. `IMG_1816 detail` - 4 frames, 3.0-7.0s detail interval

This is the closest pair. Gemma matches the subject, SUV, sedan, buildings, kiosk, distant person, and plaza. It omits the small orange A-frame sign Host sees and emits the kiosk text as the literal string 'null'; otherwise there is no major factual conflict. For downstream planning, both provide similar core scene context.

### 7. `IMG_1818 coarse` - 15 frames, [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70]

Both describe the subject, storefronts, vehicles, trees, streetlights, and street. Gemma correctly reads the mirrored storefront word 'Credit' that Host leaves unreadable, and notices a fence/railing. Its 'silver shirt' is a color error (the shirt is light gray); its vehicle labels and times are more fragmented than Host's. This is a mixed result, with Gemma stronger on this one readable sign.

### 8. `IMG_1818 detail` - 6 frames, 0.0-12.0s detail interval

Gemma recognizes the subject, cars, bollards, and bins, but largely carries the coarse urban-street summary into these six detail frames. The frames specifically show the garage entrance, lights, a parking sign, and driveway. Gemma misses the garage/sign refinement and claims 'SPEED LIMIT 20'; the visible mirrored sign is a parking restriction sign with a large 5, not that wording. Host abstains on the small text and gives a more accurate detail-specific description. The false sign text could mislead planning or OCR consumers.

### 9. `IMG_1820 coarse` - 12 frames, [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 52.445]

Both identify the man, geometric garage, and vehicles. Host correctly reads the green street sign as Chattahoochee Row and notes the brief lens occlusion at the last frame, but misreads the large mirrored storefront lettering as 'STONE BREWING CO'; the stills show 'WE ARE TENDER SPIRITS'. Gemma misses the fingertip and exact street name, and emits seven duplicate street-sign entries with the same garbled OCR string 'C_S_F_E_R_E_N_I_N_I_C_O'. Both therefore make text errors here; Gemma's repeated entities and unreadable OCR would pollute downstream object/text profiles more severely.

### 10. `IMG_1820 detail` - 6 frames, 38.0-52.55s detail interval

Host reads the mirrored wall lettering as 'WE ARE TENDER SPIRITS' and describes the visible white SUV, garage, hydrant, and street furniture. Gemma gets the subject and broad garage/vehicle context, but reports 'FINI' for the wall text, carries in the Chattahoochee Row sign and fingertip even though neither appears in these six detail frames, and gives the known vehicles and trees the entire 38-53 second span. Those inherited entities and timestamps are not grounded in this detail set. The result differs materially for signage and interval-specific planning.

## Full requests and raw answers

The following entries retain each exact prompt, image list, host response, and Gemma response from the comparison record. Gemma code fences and formatting are preserved as returned.

### 1. `IMG_1808 coarse`

Benchmark request ID: `IMG_1808:object-coarse:batch:1/1`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 92.672s; host replay wall: 110.839s; Gemma wall: 32.819s

Input images (relative to `.vision_cache/`):

- `IMG_1808/frames/frame_0000.jpg`
- `IMG_1808/frames/frame_0001.jpg`
- `IMG_1808/frames/frame_0002.jpg`
- `IMG_1808/frames/frame_0003.jpg`
- `IMG_1808/frames/frame_0004.jpg`
- `IMG_1808/frames/frame_0005.jpg`
- `IMG_1808/frames/frame_0006.jpg`

Frame timestamps (seconds): `0, 5, 10, 15, 20, 25, 27.672`

Exact prompt:

````text
These are 7 frames extracted from a 28-second video clip at the timestamps shown.

Frame timestamps: 0.0s, 5.0s, 10.0s, 15.0s, 20.0s, 25.0s, 27.7s

Identify every distinct person, vehicle, object, sign, or readable text visible in these frames.

For each entity, report:
- A specific, identifying description ("young man in black baseball cap and silver chain" not "person")
- Which frame timestamps it appears in (as appearance ranges)
- Its role: primary_subject (main focus of the clip), background (visible but not focal), passing (briefly visible)
- Its category: person, vehicle, object, text, structure
- Any readable text on signs, screens, stickers, or clothing

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
[
  {"label": "<specific description>", "appearances": [[<start_s>, <end_s>]], "role": "<primary_subject|background|passing>", "category": "<person|vehicle|object|text|structure>", "readable_text": "<any text visible on or near this entity, or null>"}
]

Rules:
- Merge continuous appearances into single ranges.
- If an entity leaves and returns, list separate appearance ranges.
- Be as specific as possible in labels (color, brand, size, distinguishing features).
- For readable text: report the exact text you can read.
````

Host answer:

````text
[{"label":"Young man with medium-brown skin, dark wavy hair under a black baseball cap, wearing a pale gray button-up shirt and silver bracelets","appearances":[[0.0,27.7]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Gray passenger-car cabin with dark leather seats, gray headliner and panoramic glass sunroof; make and model not identifiable","appearances":[[0.0,27.7]],"role":"background","category":"vehicle","readable_text":null},{"label":"Black rectangular device hanging beside the front passenger headrest, with a prominent red circular button and short chain","appearances":[[5.0,25.0]],"role":"background","category":"object","readable_text":null},{"label":"Small pale rectangular tag or card above the hanging device; markings are too small to read","appearances":[[5.0,25.0]],"role":"background","category":"object","readable_text":null},{"label":"Light concrete exterior wall with dark metal security grilles outside the car windows","appearances":[[5.0,25.0]],"role":"background","category":"structure","readable_text":null},{"label":"Dark gray seat belt crossing the man's chest","appearances":[[15.0,25.0]],"role":"background","category":"object","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "young man in black baseball cap and light grey shirt", "appearances": [[0.0, 27.7]], "role": "primary_subject", "category": "person", "readable_text": null},
  {"label": "black dashcam mounted on the rearview mirror", "appearances": [[5.0, 27.7]], "role": "background", "category": "object", "readable_text": null},
  {"label": "car interior with grey seats and sunroof", "appearances": [[0.0, 27.7]], "role": "background", "category": "vehicle", "readable_text": null},
  {"label": "silver chain bracelet on left wrist", "appearances": [[5.0, 27.7]], "role": "background", "category": "object", "readable_text": null},
  {"label": "orange and white bracelets on right wrist", "appearances": [[5.0, 27.7]], "role": "background", "category": "object", "readable_text": null},
  {"label": "warning sticker on the ceiling", "appearances": [[0.0, 1.0]], "role": "background", "category": "object", "readable_text": "WARNING"},
  {"label": "building structure visible through windows", "appearances": [[0.0, 27.7]], "role": "background", "category": "structure", "readable_text": null}
]
```
````

### 2. `IMG_1808 detail`

Benchmark request ID: `IMG_1808:object-detail:0.000-2.000:batch:0-2`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 198.870s; host replay wall: 64.693s; Gemma wall: 9.615s

Input images (relative to `.vision_cache/`):

- `IMG_1808/detail_frames/detail_0000.jpg`
- `IMG_1808/detail_frames/detail_0001.jpg`

Exact prompt:

````text
These are 2 frames spanning 0s to 2s of a 28s clip.

Known entities: Young man with medium-brown skin, dark wavy hair under a black baseball cap, wearing a pale gray button-up shirt and silver bracelets, Gray passenger-car cabin with dark leather seats, gray headliner and panoramic glass sunroof; make and model not identifiable

For each entity, refine its timestamps and add details (color, brand, text). Also report any NEW entities missed.

JSON array only, no other text:
[
  {"label": "description", "appearances": [[start, end]], "role": "primary_subject or background or passing", "category": "person or vehicle or object or text", "readable_text": "text or null"}
]
````

Host answer:

````text
[{"label":"Young man with medium-brown skin, dark wavy hair under a dark baseball cap, wearing a pale gray button-up shirt and silver chain bracelet","appearances":[[0.0,2.0]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Gray passenger-car cabin with black leather seats and a panoramic glass sunroof; no make or model visible","appearances":[[0.0,2.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Large black rectangular device suspended by a short silver chain beside the front passenger headrest, with a red round button and small silver controls","appearances":[[0.0,2.0]],"role":"background","category":"object","readable_text":null},{"label":"Pale blue-white rectangular tag attached above the hanging device; no legible writing","appearances":[[0.0,2.0]],"role":"background","category":"object","readable_text":null},{"label":"Dark gray seat belt, hanging by the driver's side pillar and then crossing the man's chest as he fastens it","appearances":[[0.0,2.0]],"role":"background","category":"object","readable_text":null},{"label":"Pair of light gray athletic shoes resting on the front passenger seat","appearances":[[0.0,2.0]],"role":"background","category":"object","readable_text":null},{"label":"White exterior wall with horizontal siding and dark square-grid window grilles seen through the car windows","appearances":[[0.0,2.0]],"role":"background","category":"object","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "Young man with medium-brown skin, dark wavy hair under a black baseball cap, wearing a pale gray button-up shirt and silver bracelets", "appearances": [[0, 2]], "role": "primary_subject", "category": "person"},
  {"label": "Gray passenger-car cabin with dark leather seats, gray headliner and panoramic glass sunroof", "appearances": [[0, 2]], "role": "background", "category": "vehicle"}
]
```
````

### 3. `IMG_1812 coarse`

Benchmark request ID: `IMG_1812:object-coarse:batch:1/2`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 76.624s; host replay wall: 60.707s; Gemma wall: 28.068s

Input images (relative to `.vision_cache/`):

- `IMG_1812/frames/frame_0000.jpg`
- `IMG_1812/frames/frame_0001.jpg`
- `IMG_1812/frames/frame_0002.jpg`
- `IMG_1812/frames/frame_0003.jpg`
- `IMG_1812/frames/frame_0004.jpg`
- `IMG_1812/frames/frame_0005.jpg`
- `IMG_1812/frames/frame_0006.jpg`
- `IMG_1812/frames/frame_0007.jpg`
- `IMG_1812/frames/frame_0008.jpg`
- `IMG_1812/frames/frame_0009.jpg`
- `IMG_1812/frames/frame_0010.jpg`
- `IMG_1812/frames/frame_0011.jpg`
- `IMG_1812/frames/frame_0012.jpg`
- `IMG_1812/frames/frame_0013.jpg`
- `IMG_1812/frames/frame_0014.jpg`

Frame timestamps (seconds): `0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70`

Exact prompt:

````text
These are 15 frames extracted from a 139-second video clip at the timestamps shown.

Frame timestamps: 0.0s, 5.0s, 10.0s, 15.0s, 20.0s, 25.0s, 30.0s, 35.0s, 40.0s, 45.0s, 50.0s, 55.0s, 60.0s, 65.0s, 70.0s

Identify every distinct person, vehicle, object, sign, or readable text visible in these frames.

For each entity, report:
- A specific, identifying description ("young man in black baseball cap and silver chain" not "person")
- Which frame timestamps it appears in (as appearance ranges)
- Its role: primary_subject (main focus of the clip), background (visible but not focal), passing (briefly visible)
- Its category: person, vehicle, object, text, structure
- Any readable text on signs, screens, stickers, or clothing

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
[
  {"label": "<specific description>", "appearances": [[<start_s>, <end_s>]], "role": "<primary_subject|background|passing>", "category": "<person|vehicle|object|text|structure>", "readable_text": "<any text visible on or near this entity, or null>"}
]

Rules:
- Merge continuous appearances into single ranges.
- If an entity leaves and returns, list separate appearance ranges.
- Be as specific as possible in labels (color, brand, size, distinguishing features).
- For readable text: report the exact text you can read.
````

Host answer:

````text
[{"label":"Young man with medium-brown skin, curly dark hair under a dark baseball cap, pale gray button-up shirt, and gray backpack strap over one shoulder","appearances":[[0.0,70.0]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Light gray backpack with padded shoulder straps worn by the man","appearances":[[5.0,70.0]],"role":"background","category":"object","readable_text":null},{"label":"Large modern multistory building with white facade, dark window grilles and rows of red-and-white triangular sunshade or decorative panels","appearances":[[5.0,70.0]],"role":"background","category":"structure","readable_text":null},{"label":"Red brick building with dark rectangular windows beside the walkway","appearances":[[15.0,60.0]],"role":"background","category":"structure","readable_text":null},{"label":"Leafy street trees with thin branches along the buildings","appearances":[[5.0,70.0]],"role":"background","category":"structure","readable_text":null},{"label":"White sedan on the street beside the buildings","appearances":[[35.0,45.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Large red stylized logo on a pale concrete building pillar; exact lettering is not legible","appearances":[[65.0,65.0]],"role":"background","category":"text","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "young man in black baseball cap and light-colored button-down shirt", "appearances": [[0.0, 70.0]], "role": "primary_subject", "category": "person", "readable_text": null},
  {"label": "gray backpack strap", "appearances": [[0.0, 70.0]], "role": "background", "category": "object", "readable_text": null},
  {"label": "modern building with metal and glass facade", "appearances": [[0.0, 70.0]], "role": "background", "category": "structure", "readable_text": null},
  {"label": "tree branches", "appearances": [[0.0, 70.0]], "role": "background", "category": "object", "readable_text": null},
  {"label": "red and white sign", "appearances": [[0.0, 70.0]], "role": "background", "category": "sign", "readable_text": "RPSV"}
]
```
````

### 4. `IMG_1812 detail`

Benchmark request ID: `IMG_1812:object-detail:0.000-2.000:batch:0-2`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 62.689s; host replay wall: 20.570s; Gemma wall: 7.223s

Input images (relative to `.vision_cache/`):

- `IMG_1812/detail_frames/detail_0000.jpg`
- `IMG_1812/detail_frames/detail_0001.jpg`

Exact prompt:

````text
These are 2 frames spanning 0s to 2s of a 139s clip.

Known entities: Young man with medium-brown skin, curly dark hair under a dark baseball cap, pale gray button-up shirt, and gray backpack strap over one shoulder

For each entity, refine its timestamps and add details (color, brand, text). Also report any NEW entities missed.

JSON array only, no other text:
[
  {"label": "description", "appearances": [[start, end]], "role": "primary_subject or background or passing", "category": "person or vehicle or object or text", "readable_text": "text or null"}
]
````

Host answer:

````text
[{"label":"Young man with medium-brown skin, curly dark hair under a dark baseball cap, pale gray button-up shirt and gray backpack","appearances":[[0.0,2.0]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Gray backpack with broad padded shoulder straps worn over one shoulder","appearances":[[0.0,2.0]],"role":"background","category":"object","readable_text":null},{"label":"Large white multistory building with dark horizontal window grilles and repeated red triangular facade elements","appearances":[[0.0,2.0]],"role":"background","category":"object","readable_text":null},{"label":"Thin leafy branches from a street tree against the sky","appearances":[[0.0,2.0]],"role":"background","category":"object","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {
    "label": "Young man with medium-brown skin, curly dark hair under a dark baseball cap, pale gray button-up shirt, and gray backpack strap over one shoulder",
    "appearances": [[0, 2]],
    "role": "primary_subject",
    "category": "person"
  }
]
```
````

### 5. `IMG_1816 coarse`

Benchmark request ID: `IMG_1816:object-coarse:batch:1/3`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 148.746s; host replay wall: 60.663s; Gemma wall: 38.785s

Input images (relative to `.vision_cache/`):

- `IMG_1816/frames/frame_0000.jpg`
- `IMG_1816/frames/frame_0001.jpg`
- `IMG_1816/frames/frame_0002.jpg`
- `IMG_1816/frames/frame_0003.jpg`
- `IMG_1816/frames/frame_0004.jpg`
- `IMG_1816/frames/frame_0005.jpg`
- `IMG_1816/frames/frame_0006.jpg`
- `IMG_1816/frames/frame_0007.jpg`
- `IMG_1816/frames/frame_0008.jpg`
- `IMG_1816/frames/frame_0009.jpg`
- `IMG_1816/frames/frame_0010.jpg`
- `IMG_1816/frames/frame_0011.jpg`
- `IMG_1816/frames/frame_0012.jpg`
- `IMG_1816/frames/frame_0013.jpg`
- `IMG_1816/frames/frame_0014.jpg`

Frame timestamps (seconds): `0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70`

Exact prompt:

````text
These are 15 frames extracted from a 189-second video clip at the timestamps shown.

Frame timestamps: 0.0s, 5.0s, 10.0s, 15.0s, 20.0s, 25.0s, 30.0s, 35.0s, 40.0s, 45.0s, 50.0s, 55.0s, 60.0s, 65.0s, 70.0s

Identify every distinct person, vehicle, object, sign, or readable text visible in these frames.

For each entity, report:
- A specific, identifying description ("young man in black baseball cap and silver chain" not "person")
- Which frame timestamps it appears in (as appearance ranges)
- Its role: primary_subject (main focus of the clip), background (visible but not focal), passing (briefly visible)
- Its category: person, vehicle, object, text, structure
- Any readable text on signs, screens, stickers, or clothing

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
[
  {"label": "<specific description>", "appearances": [[<start_s>, <end_s>]], "role": "<primary_subject|background|passing>", "category": "<person|vehicle|object|text|structure>", "readable_text": "<any text visible on or near this entity, or null>"}
]

Rules:
- Merge continuous appearances into single ranges.
- If an entity leaves and returns, list separate appearance ranges.
- Be as specific as possible in labels (color, brand, size, distinguishing features).
- For readable text: report the exact text you can read.
````

Host answer:

````text
[{"label":"Young man with medium-brown skin and dark wavy hair under a charcoal baseball cap, wearing a light gray button-up shirt, layered necklaces with a pink rectangular pendant, rings and silver bracelets","appearances":[[0.0,70.0]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Black Jeep-style four-door SUV with rear-mounted spare tire parked along the brick building","appearances":[[0.0,70.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Silver four-door sedan parked beside the black SUV","appearances":[[0.0,70.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Low red-brick building with dark windows and a doorway across the paved plaza","appearances":[[0.0,70.0]],"role":"background","category":"structure","readable_text":null},{"label":"Modern glass-and-metal building with a rust-red steel canopy, exterior stairs and red handrails","appearances":[[0.0,70.0]],"role":"background","category":"structure","readable_text":null},{"label":"Tall black digital information kiosk with a bright green screen and small unreadable white text","appearances":[[0.0,70.0]],"role":"background","category":"object","readable_text":null},{"label":"Second person in a tan top and dark pants walking across the building entrance steps in the distance","appearances":[[5.0,10.0]],"role":"passing","category":"person","readable_text":null},{"label":"Gray interlocking-paver plaza and sidewalk with a small planted bed","appearances":[[0.0,70.0]],"role":"background","category":"structure","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "young man in black baseball cap and silver chain", "appearances": [[0.0, 70.0]], "role": "primary_subject", "category": "person", "readable_text": null},
  {"label": "black SUV", "appearances": [[5.0, 70.0]], "role": "background", "category": "vehicle", "readable_text": null},
  {"label": "silver sedan", "appearances": [[5.0, 70.0]], "role": "background", "category": "vehicle", "readable_text": null},
  {"label": "brick building", "appearances": [[5.0, 70.0]], "role": "background", "category": "structure", "readable_text": null},
  {"label": "modern building with glass and metal frame", "appearances": [[5.0, 70.0]], "role": "background", "category": "structure", "readable_text": null},
  {"label": "large gray building", "appearances": [[5.0, 70.0]], "role": "background", "category": "structure", "readable_text": null},
  {"label": "paved walkway", "appearances": [[5.0, 70.0]], "role": "background", "category": "structure", "readable_text": null},
  {"label": "digital kiosk", "appearances": [[5.0, 70.0]], "role": "background", "category": "object", "readable_text": null},
  {"label": "person walking in background", "appearances": [[5.0, 70.0]], "role": "background", "category": "person", "readable_text": null}
]
```
````

### 6. `IMG_1816 detail`

Benchmark request ID: `IMG_1816:object-detail:3.000-7.000:batch:0-4`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 78.641s; host replay wall: 50.666s; Gemma wall: 28.839s

Input images (relative to `.vision_cache/`):

- `IMG_1816/detail_frames/detail_0000.jpg`
- `IMG_1816/detail_frames/detail_0001.jpg`
- `IMG_1816/detail_frames/detail_0002.jpg`
- `IMG_1816/detail_frames/detail_0003.jpg`

Exact prompt:

````text
These are 4 frames spanning 3s to 7s of a 189s clip.

Known entities: Young man with medium-brown skin and dark wavy hair under a charcoal baseball cap, wearing a light gray button-up shirt, layered necklaces with a pink rectangular pendant, rings and silver bracelets, Black Jeep-style four-door SUV with rear-mounted spare tire parked along the brick building, Silver four-door sedan parked beside the black SUV, Low red-brick building with dark windows and a doorway across the paved plaza, Modern glass-and-metal building with a rust-red steel canopy, exterior stairs and red handrails, Tall black digital information kiosk with a bright green screen and small unreadable white text, Second person in a tan top and dark pants walking across the building entrance steps in the distance, Gray interlocking-paver plaza and sidewalk with a small planted bed

For each entity, refine its timestamps and add details (color, brand, text). Also report any NEW entities missed.

JSON array only, no other text:
[
  {"label": "description", "appearances": [[start, end]], "role": "primary_subject or background or passing", "category": "person or vehicle or object or text", "readable_text": "text or null"}
]
````

Host answer:

````text
[{"label":"Young man with medium-brown skin and wavy dark hair under a charcoal baseball cap, wearing a light gray button-up shirt, silver necklaces with a pale pink rectangular pendant, rings and chain bracelets","appearances":[[3.0,7.0]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Black Jeep-style SUV with rear spare tire, parked beside a silver sedan","appearances":[[3.0,7.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Silver sedan parked beside the black SUV","appearances":[[3.0,7.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Second person wearing a tan jacket or top and dark trousers crossing in front of the stairs","appearances":[[3.0,7.0]],"role":"passing","category":"person","readable_text":null},{"label":"Modern building entrance with rust-red steel canopy and red-railed gray stairs beside a brick building","appearances":[[3.0,7.0]],"role":"background","category":"object","readable_text":null},{"label":"Tall black digital kiosk with an illuminated green screen and small white text that is not legible","appearances":[[3.0,7.0]],"role":"background","category":"object","readable_text":null},{"label":"Small orange A-frame sign beside the lower stairs; text is too small to read","appearances":[[3.0,7.0]],"role":"background","category":"object","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "Young man with medium-brown skin and dark wavy hair under a charcoal baseball cap, wearing a light gray button-up shirt, layered necklaces with a pink rectangular pendant, rings and silver bracelets", "appearances": [[3, 7]], "role": "primary_subject", "category": "person"},
  {"label": "Black Jeep-style four-door SUV with rear-mounted spare tire parked along the brick building", "appearances": [[3, 7]], "role": "background", "category": "vehicle"},
  {"label": "Silver four-door sedan parked beside the black SUV", "appearances": [[3, 7]], "role": "background", "category": "vehicle"},
  {"label": "Low red-brick building with dark windows and a doorway across the paved plaza", "appearances": [[3, 7]], "role": "background", "category": "object"},
  {"label": "Modern glass-and-metal building with a rust-red steel canopy, exterior stairs and red handrails", "appearances": [[3, 7]], "role": "background", "category": "object"},
  {"label": "Tall black digital information kiosk with a bright green screen and small unreadable white text", "appearances": [[3, 7]], "role": "background", "category": "object", "readable_text": "null"},
  {"label": "Second person in a tan top and dark pants walking across the building entrance steps in the distance", "appearances": [[3, 7]], "role": "passing", "category": "person"},
  {"label": "Gray interlocking-paver plaza and sidewalk with a small planted bed", "appearances": [[3, 7]], "role": "background", "category": "object"}
]
```
````

### 7. `IMG_1818 coarse`

Benchmark request ID: `IMG_1818:object-coarse:batch:1/2`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 66.622s; host replay wall: 50.651s; Gemma wall: 41.431s

Input images (relative to `.vision_cache/`):

- `IMG_1818/frames/frame_0000.jpg`
- `IMG_1818/frames/frame_0001.jpg`
- `IMG_1818/frames/frame_0002.jpg`
- `IMG_1818/frames/frame_0003.jpg`
- `IMG_1818/frames/frame_0004.jpg`
- `IMG_1818/frames/frame_0005.jpg`
- `IMG_1818/frames/frame_0006.jpg`
- `IMG_1818/frames/frame_0007.jpg`
- `IMG_1818/frames/frame_0008.jpg`
- `IMG_1818/frames/frame_0009.jpg`
- `IMG_1818/frames/frame_0010.jpg`
- `IMG_1818/frames/frame_0011.jpg`
- `IMG_1818/frames/frame_0012.jpg`
- `IMG_1818/frames/frame_0013.jpg`
- `IMG_1818/frames/frame_0014.jpg`

Frame timestamps (seconds): `0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70`

Exact prompt:

````text
These are 15 frames extracted from a 86-second video clip at the timestamps shown.

Frame timestamps: 0.0s, 5.0s, 10.0s, 15.0s, 20.0s, 25.0s, 30.0s, 35.0s, 40.0s, 45.0s, 50.0s, 55.0s, 60.0s, 65.0s, 70.0s

Identify every distinct person, vehicle, object, sign, or readable text visible in these frames.

For each entity, report:
- A specific, identifying description ("young man in black baseball cap and silver chain" not "person")
- Which frame timestamps it appears in (as appearance ranges)
- Its role: primary_subject (main focus of the clip), background (visible but not focal), passing (briefly visible)
- Its category: person, vehicle, object, text, structure
- Any readable text on signs, screens, stickers, or clothing

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
[
  {"label": "<specific description>", "appearances": [[<start_s>, <end_s>]], "role": "<primary_subject|background|passing>", "category": "<person|vehicle|object|text|structure>", "readable_text": "<any text visible on or near this entity, or null>"}
]

Rules:
- Merge continuous appearances into single ranges.
- If an entity leaves and returns, list separate appearance ranges.
- Be as specific as possible in labels (color, brand, size, distinguishing features).
- For readable text: report the exact text you can read.
````

Host answer:

````text
[{"label":"Young man with medium-brown skin and dark wavy hair under a dark baseball cap, wearing a pale gray collared button-up shirt and a gray backpack","appearances":[[0.0,70.0]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Gray backpack with padded shoulder straps over the man's shoulders","appearances":[[0.0,70.0]],"role":"background","category":"object","readable_text":null},{"label":"Urban commercial street lined with red-brick storefronts, dark storefront windows, doors and sidewalk railings","appearances":[[0.0,70.0]],"role":"background","category":"structure","readable_text":null},{"label":"Gray and white passenger cars along the street, including a car behind the subject and vehicles approaching with headlights","appearances":[[0.0,70.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Tall leafy trees silhouetted against a bright orange-pink sunset sky","appearances":[[0.0,70.0]],"role":"background","category":"structure","readable_text":null},{"label":"Black street lamps with warm illuminated fixtures along the sidewalk","appearances":[[0.0,70.0]],"role":"background","category":"object","readable_text":null},{"label":"Bright green and multicolored storefront signs and window lettering, blurred or reversed and not reliably legible","appearances":[[15.0,70.0]],"role":"background","category":"text","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "young man in black baseball cap and silver shirt", "appearances": [[0.0, 70.0]], "role": "primary_subject", "category": "person", "readable_text": null},
  {"label": "dark gray car", "appearances": [[0.0, 10.0]], "role": "background", "category": "vehicle", "readable_text": null},
  {"label": "brick building with glass windows", "appearances": [[0.0, 70.0]], "role": "background", "category": "structure", "readable_text": null},
  {"label": "trees in the background", "appearances": [[0.0, 70.0]], "role": "background", "category": "object", "readable_text": null},
  {"label": "street lights", "appearances": [[0.0, 70.0]], "role": "background", "category": "object", "readable_text": null},
  {"label": "building with 'Credit' sign", "appearances": [[10.0, 30.0]], "role": "background", "category": "structure", "readable_text": "Credit"},
  {"label": "dark SUV", "appearances": [[30.0, 40.0]], "role": "background", "category": "vehicle", "readable_text": null},
  {"label": "fence and railing", "appearances": [[30.0, 40.0]], "role": "background", "category": "structure", "readable_text": null},
  {"label": "street and sidewalk", "appearances": [[0.0, 70.0]], "role": "background", "category": "structure", "readable_text": null}
]
```
````

### 8. `IMG_1818 detail`

Benchmark request ID: `IMG_1818:object-detail:0.000-12.000:batch:0-6`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 90.650s; host replay wall: 52.665s; Gemma wall: 35.904s

Input images (relative to `.vision_cache/`):

- `IMG_1818/detail_frames/detail_0000.jpg`
- `IMG_1818/detail_frames/detail_0001.jpg`
- `IMG_1818/detail_frames/detail_0002.jpg`
- `IMG_1818/detail_frames/detail_0003.jpg`
- `IMG_1818/detail_frames/detail_0004.jpg`
- `IMG_1818/detail_frames/detail_0005.jpg`

Exact prompt:

````text
These are 6 frames spanning 0s to 12s of a 86s clip.

Known entities: Young man with medium-brown skin and dark wavy hair under a dark baseball cap, wearing a pale gray collared button-up shirt and a gray backpack, Gray backpack with padded shoulder straps over the man's shoulders, Urban commercial street lined with red-brick storefronts, dark storefront windows, doors and sidewalk railings, Gray and white passenger cars along the street, including a car behind the subject and vehicles approaching with headlights, Tall leafy trees silhouetted against a bright orange-pink sunset sky, Black street lamps with warm illuminated fixtures along the sidewalk

For each entity, refine its timestamps and add details (color, brand, text). Also report any NEW entities missed.

JSON array only, no other text:
[
  {"label": "description", "appearances": [[start, end]], "role": "primary_subject or background or passing", "category": "person or vehicle or object or text", "readable_text": "text or null"}
]
````

Host answer:

````text
[{"label":"Young man with medium-brown skin, dark wavy hair under a charcoal baseball cap, pale gray collared shirt and a gray backpack","appearances":[[0.0,12.0]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Gray backpack with broad padded straps and a small purple patch on a shoulder strap","appearances":[[0.0,12.0]],"role":"background","category":"object","readable_text":null},{"label":"Multilevel concrete parking garage with red-and-white triangular facade panels and warm strings of lights over its entrance","appearances":[[0.0,12.0]],"role":"background","category":"object","readable_text":null},{"label":"Red-brick commercial building with illuminated doorways and windows","appearances":[[0.0,12.0]],"role":"background","category":"object","readable_text":null},{"label":"White SUV or pickup and other passenger cars near the parking entrance and street","appearances":[[0.0,12.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Row of white flexible bollards with black bases along the driveway","appearances":[[0.0,12.0]],"role":"background","category":"object","readable_text":null},{"label":"Green commercial dumpsters beside the wooded edge of the lot","appearances":[[0.0,12.0]],"role":"background","category":"object","readable_text":null},{"label":"White rectangular roadside parking sign on a wooden post; the small reversed lettering is not confidently legible","appearances":[[5.0,12.0]],"role":"background","category":"text","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "Young man with medium-brown skin and dark wavy hair under a dark baseball cap, wearing a pale gray collared button-up shirt and a gray backpack", "appearances": [[0, 12]], "role": "primary_subject", "category": "person"},
  {"label": "Gray backpack with padded shoulder straps over the man's shoulders", "appearances": [[0, 12]], "role": "background", "category": "object"},
  {"label": "Urban commercial street lined with red-brick storefronts, dark storefront windows, doors and sidewalk railings", "appearances": [[0, 12]], "role": "background", "category": "object"},
  {"label": "Gray and white passenger cars along the street, including a car behind the subject and vehicles approaching with headlights", "appearances": [[0, 12]], "role": "background", "category": "vehicle"},
  {"label": "Tall leafy trees silhouetted against a bright orange-pink sunset sky", "appearances": [[0, 12]], "role": "background", "category": "object"},
  {"label": "Black street lamps with warm illuminated fixtures along the sidewalk", "appearances": [[0, 12]], "role": "background", "category": "object"},
  {"label": "Red-brick storefront", "appearances": [[0, 12]], "role": "background", "category": "object"},
  {"label": "White parking lines", "appearances": [[0, 12]], "role": "background", "category": "object"},
  {"label": "Green trash bin", "appearances": [[0, 12]], "role": "background", "category": "object"},
  {"label": "White bollards", "appearances": [[0, 12]], "role": "background", "category": "object"},
  {"label": "Sign with text", "appearances": [[0, 12]], "role": "background", "category": "text", "readable_text": "SPEED LIMIT 20"}
]
```
````

### 9. `IMG_1820 coarse`

Benchmark request ID: `IMG_1820:object-coarse:batch:1/1`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 80.646s; host replay wall: 70.707s; Gemma wall: 55.577s

Input images (relative to `.vision_cache/`):

- `IMG_1820/frames/frame_0000.jpg`
- `IMG_1820/frames/frame_0001.jpg`
- `IMG_1820/frames/frame_0002.jpg`
- `IMG_1820/frames/frame_0003.jpg`
- `IMG_1820/frames/frame_0004.jpg`
- `IMG_1820/frames/frame_0005.jpg`
- `IMG_1820/frames/frame_0006.jpg`
- `IMG_1820/frames/frame_0007.jpg`
- `IMG_1820/frames/frame_0008.jpg`
- `IMG_1820/frames/frame_0009.jpg`
- `IMG_1820/frames/frame_0010.jpg`
- `IMG_1820/frames/frame_0011.jpg`

Frame timestamps (seconds): `0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 52.445`

Exact prompt:

````text
These are 12 frames extracted from a 53-second video clip at the timestamps shown.

Frame timestamps: 0.0s, 5.0s, 10.0s, 15.0s, 20.0s, 25.0s, 30.0s, 35.0s, 40.0s, 45.0s, 50.0s, 52.4s

Identify every distinct person, vehicle, object, sign, or readable text visible in these frames.

For each entity, report:
- A specific, identifying description ("young man in black baseball cap and silver chain" not "person")
- Which frame timestamps it appears in (as appearance ranges)
- Its role: primary_subject (main focus of the clip), background (visible but not focal), passing (briefly visible)
- Its category: person, vehicle, object, text, structure
- Any readable text on signs, screens, stickers, or clothing

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
[
  {"label": "<specific description>", "appearances": [[<start_s>, <end_s>]], "role": "<primary_subject|background|passing>", "category": "<person|vehicle|object|text|structure>", "readable_text": "<any text visible on or near this entity, or null>"}
]

Rules:
- Merge continuous appearances into single ranges.
- If an entity leaves and returns, list separate appearance ranges.
- Be as specific as possible in labels (color, brand, size, distinguishing features).
- For readable text: report the exact text you can read.
````

Host answer:

````text
[{"label":"Young man with medium-brown skin, dark wavy hair under a charcoal cap, wearing a pale gray button-up shirt and gray backpack","appearances":[[0.0,52.4]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Gray backpack with wide padded shoulder straps","appearances":[[0.0,50.0]],"role":"background","category":"object","readable_text":null},{"label":"Large multilevel parking garage with a pale facade, repeated triangular openings and red accent panels","appearances":[[0.0,50.0]],"role":"background","category":"structure","readable_text":null},{"label":"Commercial red-brick building with large painted white lettering, windows and lit storefront entrances","appearances":[[10.0,50.0]],"role":"background","category":"structure","readable_text":"STONE BREWING CO"},{"label":"Green street sign reading Chattahoochee Row","appearances":[[50.0,50.0]],"role":"background","category":"text","readable_text":"Chattahoochee Row"},{"label":"White passenger SUV and gray or tan SUV parked along the street near the garage","appearances":[[30.0,50.0]],"role":"background","category":"vehicle","readable_text":null},{"label":"Rows of warm string lights over the parking and storefront entrance","appearances":[[0.0,30.0]],"role":"background","category":"object","readable_text":null},{"label":"Leafy trees and tall narrow evergreen trees beside the buildings","appearances":[[0.0,50.0]],"role":"background","category":"structure","readable_text":null},{"label":"Reddish fingertip briefly covering the camera lens","appearances":[[52.4,52.4]],"role":"passing","category":"object","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "young man in black baseball cap and silver chain", "appearances": [[0.0, 52.4]], "role": "primary_subject", "category": "person", "readable_text": null},
  {"label": "modern building with geometric facade", "appearances": [[0.0, 52.4]], "role": "background", "category": "structure", "readable_text": null},
  {"label": "white SUV", "appearances": [[40.0, 52.4]], "role": "background", "category": "vehicle", "readable_text": null},
  {"label": "black SUV", "appearances": [[40.0, 52.4]], "role": "background", "category": "vehicle", "readable_text": null},
  {"label": "street sign", "appearances": [[35.0, 52.4]], "role": "background", "category": "sign", "readable_text": "C_S_F_E_R_E_N_I_N_I_C_O"},
  {"label": "street sign", "appearances": [[35.0, 52.4]], "role": "background", "category": "sign", "readable_text": "C_S_F_E_R_E_N_I_N_I_C_O"},
  {"label": "street sign", "appearances": [[35.0, 52.4]], "role": "background", "category": "sign", "readable_text": "C_S_F_E_R_E_N_I_N_I_C_O"},
  {"label": "street sign", "appearances": [[35.0, 52.4]], "role": "background", "category": "sign", "readable_text": "C_S_F_E_R_E_N_I_N_I_C_O"},
  {"label": "street sign", "appearances": [[35.0, 52.4]], "role": "background", "category": "sign", "readable_text": "C_S_F_E_R_E_N_I_N_I_C_O"},
  {"label": "street sign", "appearances": [[35.0, 52.4]], "role": "background", "category": "sign", "readable_text": "C_S_F_E_R_E_N_I_N_I_C_O"},
  {"label": "street sign", "appearances": [[35.0, 52.4]], "role": "background", "category": "sign", "readable_text": "C_S_F_E_R_E_N_I_N_I_C_O"},
  {"label": "street sign", "appearances": [[35.0, 52.4]], "role": "background", "category": "sign", "readable_text
````

### 10. `IMG_1820 detail`

Benchmark request ID: `IMG_1820:object-detail:38.000-52.550:batch:0-6`
Host route: `host` / `host:agent`
Gemma route: `mlx_vlm` / `mlx-community/gemma-4-12b-it-4bit`
Historical host wall: 86.644s; host replay wall: 134.967s; Gemma wall: 29.548s

Input images (relative to `.vision_cache/`):

- `IMG_1820/detail_frames/detail_0000.jpg`
- `IMG_1820/detail_frames/detail_0001.jpg`
- `IMG_1820/detail_frames/detail_0002.jpg`
- `IMG_1820/detail_frames/detail_0003.jpg`
- `IMG_1820/detail_frames/detail_0004.jpg`
- `IMG_1820/detail_frames/detail_0005.jpg`

Exact prompt:

````text
These are 6 frames spanning 38s to 53s of a 53s clip.

Known entities: Young man with medium-brown skin, dark wavy hair under a charcoal cap, wearing a pale gray button-up shirt and gray backpack, Gray backpack with wide padded shoulder straps, Large multilevel parking garage with a pale facade, repeated triangular openings and red accent panels, Commercial red-brick building with large painted white lettering, windows and lit storefront entrances, Green street sign reading Chattahoochee Row, White passenger SUV and gray or tan SUV parked along the street near the garage, Leafy trees and tall narrow evergreen trees beside the buildings, Reddish fingertip briefly covering the camera lens

For each entity, refine its timestamps and add details (color, brand, text). Also report any NEW entities missed.

JSON array only, no other text:
[
  {"label": "description", "appearances": [[start, end]], "role": "primary_subject or background or passing", "category": "person or vehicle or object or text", "readable_text": "text or null"}
]
````

Host answer:

````text
[{"label":"Young man with medium-brown skin, dark wavy hair under a dark charcoal baseball cap, wearing a light gray collared button-up shirt and gray backpack with broad straps","appearances":[[38,53]],"role":"primary_subject","category":"person","readable_text":null},{"label":"Large multilevel parking garage with a pale facade patterned by repeated triangular openings, a glazed stair/elevator tower, and red accent panels","appearances":[[38,50]],"role":"background","category":"object","readable_text":null},{"label":"Red-brick commercial building with white painted lettering and a white infinity-like emblem; the selfie-mirrored lettering reads WE ARE TENDER SPIRITS","appearances":[[38,47]],"role":"background","category":"text","readable_text":"WE ARE TENDER SPIRITS"},{"label":"White passenger SUV moving along the street in front of the garage","appearances":[[41,50]],"role":"passing","category":"vehicle","readable_text":null},{"label":"Red fire hydrant beside the sidewalk and planted strip","appearances":[[47,53]],"role":"background","category":"object","readable_text":null},{"label":"Tall brown streetlight pole with a curved black lamp head","appearances":[[47,53]],"role":"background","category":"object","readable_text":null},{"label":"Sidewalk, curb, roadway, and narrow landscaped planting beds alongside the brick building","appearances":[[38,53]],"role":"background","category":"object","readable_text":null}]
````

Gemma answer:

````text
```json
[
  {"label": "Young man with medium-brown skin, dark wavy hair under a charcoal cap, wearing a pale gray button-up shirt and gray backpack", "appearances": [[38, 53]], "role": "primary_subject", "category": "person"},
  {"label": "Gray backpack with wide padded shoulder straps", "appearances": [[38, 53]], "role": "background", "category": "object"},
  {"label": "Large multilevel parking garage with a pale facade, repeated triangular openings and red accent panels", "appearances": [[38, 53]], "role": "background", "category": "building"},
  {"label": "Commercial red-brick building with large painted white lettering, windows and lit storefront entrances", "appearances": [[38, 53]], "role": "background", "category": "building", "readable_text": "FINI"},
  {"label": "Green street sign reading Chattahoochee Row", "appearances": [[38, 53]], "role": "background", "category": "sign", "readable_text": "Chattahoochee Row"},
  {"label": "White passenger SUV and gray or tan SUV parked along the street near the garage", "appearances": [[38, 53]], "role": "background", "category": "vehicle"},
  {"label": "Leafy trees and tall narrow evergreen trees beside the buildings", "appearances": [[38, 53]], "role": "background", "category": "plant"},
  {"label": "Reddish fingertip briefly covering the camera lens", "appearances": [[48, 53]], "role": "background", "category": "object"}
]
```
````

## Decision requested

`needs-decision [key=stills-route-vep-stills-gemma-vs-cloud]`: the sample does not meet the same-quality bar; Gemma is much faster by recorded wall time but has repeated detail omissions, unsupported OCR, duplicate entities, and detail-window carryover. Please decide whether to keep the mixed route or request a further comparison. No product code was changed pending this decision.
