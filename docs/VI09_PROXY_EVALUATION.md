# VI-09 proxy evaluation

The annotated proxy fixture is `tests/fixtures/scene_cinematography/annotated_proxy.json`.
It defines a 12-second source with two known shot cuts and three labeled shot
profiles. The fixture is synthetic and exists to exercise boundary scoring,
hierarchy separation, and measurement citations without claiming real footage
coverage.

| Measure | Proxy result | Basis |
|---|---:|---|
| Shot-boundary F1 | 1.00 | 2 of 2 annotated boundaries matched within 50 ms; no extra boundaries |
| Sub-shot-boundary F1 | 1.00 | 1 of 1 annotated camera-mode transition matched within 50 ms; no extra boundaries |
| Cinematographic attribute agreement | 1.00 | 6 of 6 framing and camera-movement labels agreed |

The real annotated scene/shot corpus is unavailable. Real-corpus boundary F1
and attribute agreement remain pending and are not claimed by this fixture.

The deterministic pass runs first and uses the existing ffmpeg cut scores,
frame-difference motion, decomposed optical flow, face width, and color curves.
Every non-unknown attribute names the sampled measurement IDs that support it.
Cut intervals are measured, sub-shot intervals inferred from motion-mode
changes, and M4 scene/sequence groupings interpreted; these certainty values
remain separate from confidence and coverage.
Sequence intervals in this baseline are explicitly limited to adjacent M4
scenes with matching location and type; they are structural hypotheses, not
claims that a larger narrative event was established. This lane downloads no
model. Project Model interpretation records cite the interval observations
that preserve those measurements and hypotheses.
