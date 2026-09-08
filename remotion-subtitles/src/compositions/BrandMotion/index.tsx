/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill, OffthreadVideo, staticFile } from "remotion";

/**
 * Brand motion, played whole.
 *
 * The first composition in this project that reads a video file: a
 * project-supplied brand sting (a logo reveal, a transition bumper),
 * staged to `public/brand/` and played full frame with its own alpha.
 * The engine draws it and does not supply it - `src` is a file out of
 * the project's own brand assets or the props were refused before they
 * reached here, never a placeholder (AGENTS.md 14).
 *
 * What this composition does NOT do, on purpose:
 *
 * - No conform. It plays the staged file in wall-clock time at the
 *   composition rate (each frame seeks the source timestamp), which is
 *   the `native_sample` strategy: authored pixels, stepped cadence.
 *   Which strategy a render uses is declared Python-side
 *   (`library/tools/brand_motion.py`) and there is no prop for it,
 *   because a prop the renderer reads would be a second place the
 *   choice could be made.
 * - No fit. The source geometry must equal the delivery frame - the
 *   props builder refuses a mismatch rather than letterboxing or
 *   cropping, because both change how the mark reads and that is
 *   framing taste, not rendering.
 * - No trim. `durationInFrames` is the file's whole measured length, so
 *   a reel that needs a shorter sting needs a shorter asset.
 *
 * The staged file is a same-rate VP9 WebM mezzanine, not the project's
 * ProRes master: the renderer's browser exposes no decodable video
 * track on ProRes in QuickTime (measured - `videoWidth` 0), so the raw
 * file would composite as nothing, silently. See
 * `library/tools/brand_motion.py` and `docs/BRAND_MOTION_MEASURED.md`.
 */
export type BrandMotionProps = {
  /**
   * A project-supplied file this composition plays, as a path under
   * Remotion's `public/` - `brand/<name>`, staged there from the
   * project's own brand assets via its same-rate VP9 mezzanine.
   * `staticFile` turns it into the URL the render loads; Python states
   * the path and never the URL, because how `public/` is served is
   * Remotion's business and not the pipeline's. Empty renders nothing,
   * so a props file hand-edited in the studio cannot take the render
   * down - the same reason MotionGraphics draws null for an element it
   * has no arm for.
   */
  src: string;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  /**
   * Whether the brand sound plays. REQUIRED with no default: the real
   * assets carry an audio stream each, and silence vs sound is a choice
   * nobody made on the engine's behalf. Stated Python-side and refused
   * there when absent.
   */
  muted: boolean;
  /**
   * Playback gain. 1.0 is the absence of a mix decision, not a choice
   * of one - the way CUT_TYPES is the absence of decoration (AGENTS.md
   * 10.5). Anything else is a mix and belongs to whoever mixes.
   */
  volume?: number;
};

export const brandMotionSchema = {} as any;

export const BrandMotion: React.FC<BrandMotionProps> = ({
  src,
  muted,
  volume = 1.0,
}) => {
  if (!src) return null;
  return (
    <AbsoluteFill>
      <OffthreadVideo
        src={staticFile(src)}
        muted={muted}
        volume={volume}
        style={{ width: "100%", height: "100%", objectFit: "fill" }}
      />
    </AbsoluteFill>
  );
};
