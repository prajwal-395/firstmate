/**
 * FullFrameCard - the whole frame, for a bounded stretch, IN PLACE OF picture.
 *
 * Every other composition in this project is an OVERLAY: SubtitleOverlay,
 * MotionGraphics and TimedTextOverlay all render with `--transparent` and
 * are composited over footage that keeps playing underneath. This one is
 * not. It draws its own ground and is placed on the reel's own picture
 * track (V1) for its own stretch of reel time, so there is nothing
 * beneath it and nothing to composite over.
 *
 * That difference is the reason it is a separate composition rather than
 * a MotionGraphics element with a "full frame" footprint. An overlay at
 * full opacity would hide the picture and leave the SOUND playing - and
 * the reels path cannot turn that sound down, because Resolve's scripting
 * API exposes no audio level at all (AGENTS.md 5). See
 * `library/tools/full_frame_element.py`, which is the one enumeration.
 *
 * It states nothing of its own. The ground, the typeface, every colour,
 * every word, the hold and the motion character all arrive in props from
 * a declaration the project wrote. There is no default background, no
 * default colour and no default copy here, for the reason `house_look.py`
 * was emptied (AGENTS.md 10.5). The only value this file supplies is what
 * a named motion character LOOKS like, and it imports those from
 * MotionGraphics rather than respelling them - one drawing of `blur` in
 * this engine, not two.
 */
import React from "react";
import { AbsoluteFill, Img, staticFile, useCurrentFrame } from "remotion";
import { loadBundledFonts, loadProjectFont } from "../../fonts";
import {
  elementOpacity,
  entranceTransform,
  exitTransform,
  rampFrames,
  typewriterCursorOn,
  typewriterShown,
  typewriterSplit,
} from "../MotionGraphics";

/** One word the reveal lands on, measured at plan time.
 *
 * `start`/`end` are seconds on the CARD's own clock (frame-exact on the
 * plan's own rounding), and `chars` is the cumulative characters shown
 * through the end of that word, counted over the resolved runs in the
 * order they reveal. The composition owns the time-to-progress mapping
 * and never aligns text itself: it shows `chars` of the last cue whose
 * `start` has passed, so a cue boundary always coincides with a word
 * boundary on screen. Planned by
 * `library/tools/full_frame_element.py` (`word_sync`), which refuses
 * the segment rather than emitting cues its runs cannot match.
 */
export type WordCue = {
  word: string;
  start: number;
  end: number;
  chars: number;
};

export type CardRun = {
  text: string;
  /** `display` | `supporting` | `micro` - the vocabulary's own axis. */
  type_role: string;
  colour: string;
  /** Pixels of the delivery frame, when the declaration states one. */
  font_size?: number;
};

export type FullFrameCardProps = {
  runs: CardRun[];
  /** The card's own ground. Required: a full-frame element IS the picture. */
  background: string;
  entrance: string;
  exit: string;
  fontFamily: string;
  fontFile?: string;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  safeArea: { top: number; right: number; bottom: number; left: number };
  /**
   * Normalised vertical centre of the stack on the delivery frame.
   *
   * Absent means centred in the safe box, which is the ABSENCE of a
   * position rather than a chosen one: unlike a timed text moment there
   * is no picture underneath and no letterbox bar to clear, so there is
   * nothing for a position to be measured against.
   */
  y?: number;
  /**
   * A project-supplied still this card draws above its runs, as a path
   * under Remotion's `public/` - `brand/<name>`, staged there from the
   * project's own `brand_assets/`. `staticFile` turns it into the URL
   * the render loads; Python states the path and never the URL, because
   * how `public/` is served is Remotion's business and not the
   * pipeline's. Absent for every card that draws no image.
   *
   * The engine ships no artwork and states none (AGENTS.md 14), so this
   * is a path the PROJECT supplied or the card was refused before it
   * reached these props - never a placeholder.
   */
  image?: string;
  /** The image's width in pixels of the delivery frame, when stated. */
  imageWidth?: number;
  /**
   * The segment's own word clock, one cue per spoken word. Absent for
   * every card and every span segment that was planned time-based -
   * which is what keeps props written before this slot existed
   * rendering byte-identically: no key, no change of path.
   */
  wordCues?: WordCue[];
};

export const fullFrameCardSchema = {} as any;

/** The pixel size of each typographic weight, when a run states none.
 *
 * The same three the overlay roster names, at the same sizes MotionGraphics
 * draws them - a card is read at the same distance as a title lockup. A
 * run that states its own `font_size` overrides this; the declaration owns
 * the magnitude and this is only what the WEIGHT means in pixels when it
 * does not.
 */
const TYPE_SIZE: Record<string, number> = {
  display: 56,
  supporting: 36,
  micro: 24,
};

const TYPE_WEIGHT: Record<string, number> = {
  display: 900,
  supporting: 700,
  micro: 600,
};

/** The entrances a word clock can pace - the three reveals
 * `docs/ANIMATED_REEL_CEILING.md` names as time-based across a card.
 * The plan refuses `word_sync` beside any other entrance, so this list
 * agreeing with `full_frame_element.WORD_CUED_ENTRANCES` is load-bearing;
 * a character added here without its plan-side refusal would run
 * time-based beside cues that claim otherwise.
 */
export const WORD_CUED_ENTRANCES = ["typewriter", "mask", "draw"];

/** Characters shown at `frame` off the word clock.
 *
 * The last cue whose `start` has passed owns the frame - karaoke
 * reading, not interpolation, so the reveal steps exactly on word
 * starts and holds between them. Clamped to `totalChars`: cues count
 * over the resolved runs, so anything past the end is a plan defect
 * contained here rather than drawn past the text.
 */
export const wordCuedChars = (
  frame: number,
  fps: number,
  cues: WordCue[],
  totalChars: number,
): number => {
  if (!cues || cues.length === 0 || totalChars <= 0) return 0;
  const t = fps > 0 ? frame / fps : 0;
  const ordered = [...cues].sort((a, b) => a.start - b.start);
  let shown = 0;
  for (const cue of ordered) {
    if (t >= cue.start) shown = Math.min(cue.chars, totalChars);
  }
  return Math.max(0, shown);
};

/** The entrance transform at word progress `p`, 0..1.
 *
 * The same drawing `entranceTransform` in MotionGraphics gives `mask`
 * and `draw`, with the word clock substituted for the ramp clock - one
 * drawing of each character, not two. `typewriter` needs none: it is a
 * reveal, not a transform, and the split below already reads the cues.
 */
export const wordCuedEntrance = (
  progress: number,
  entrance: string,
): React.CSSProperties => {
  const p = Math.min(1, Math.max(0, progress));
  if (entrance === "mask") {
    return { clipPath: `inset(0 ${(1 - p) * 100}% 0 0)` };
  }
  if (entrance === "draw") {
    return {
      transform: `scale(${0.85 + 0.15 * p})`,
      filter: `blur(${(1 - p) * 4}px)`,
    };
  }
  return {};
};

/** Gap between two stacked runs, in pixels.
 *
 * A gap rather than a row pitch, for the reason MotionGraphics' own
 * STACK_GAP_PX is: a fixed pitch draws one run through another the moment
 * a run wraps to two lines.
 */
const RUN_GAP_PX = 20;

export const FullFrameCard: React.FC<FullFrameCardProps> = ({
  runs,
  background,
  entrance,
  exit,
  fontFamily,
  fontFile,
  fps,
  width,
  height,
  durationInFrames,
  safeArea,
  y,
  image,
  imageWidth,
  wordCues,
}) => {
  const frame = useCurrentFrame();

  // No default, and no substitution. Same refusal as TimedTextOverlay:
  // library/tools/safe_area.py owns the insets and inventing one here is
  // the "three more hardcoded margins" the captain's ruling of 2026-08-25
  // forbade.
  if (!safeArea) {
    throw new Error(
      "FullFrameCard props carry no safeArea. " +
        "library/tools/full_frame_element.py resolves it from " +
        "library/tools/safe_area.py. Refusing to substitute a margin.",
    );
  }
  if (!background) {
    throw new Error(
      "FullFrameCard props carry no background. A full-frame element IS " +
        "the picture, so it carries its own ground; there is nothing " +
        "underneath for an alpha channel to reveal and this composition " +
        "declares no colour of its own.",
    );
  }

  // BLOCKING, and throwing on a miss - a substituted face is a valid
  // picture of the right size that nothing downstream can tell apart.
  loadBundledFonts();
  if (fontFile) {
    loadProjectFont(fontFamily, fontFile);
  }

  const opacity = elementOpacity(frame, durationInFrames, entrance, exit);

  // The word clock, when the plan measured one. `wordCues` present and
  // the entrance one of the three it can pace: the reveal steps on word
  // starts instead of running across the card's own seconds. Absent cues
  // take the frame-clock path exactly as before - no key, no change of
  // path, which is what keeps old props rendering byte-identically.
  // The exit half stays on the frame clock either way: the exit runs
  // after the words are spoken, where no word starts to land on.
  const runLengths = runs.map((run) => run.text.length);
  const totalRunChars = runLengths.reduce((sum, n) => sum + n, 0);
  const cued =
    !!wordCues &&
    wordCues.length > 0 &&
    WORD_CUED_ENTRANCES.includes(entrance);
  let reveal: number;
  let wordProgress = 1;
  if (cued) {
    wordProgress =
      totalRunChars > 0
        ? wordCuedChars(frame, fps, wordCues as WordCue[], totalRunChars) /
          totalRunChars
        : 1;
    // The exit half of `typewriterShown`, with the word progress standing
    // in for the entrance half: the exit wins while its ramp is active,
    // the words own every frame before it - the same precedence the
    // shared helper gives, so the two cannot disagree about an end.
    const outFrames = exit === "typewriter" ? rampFrames(exit) : 0;
    reveal =
      outFrames > 0 && durationInFrames - frame < outFrames
        ? Math.min(1, Math.max(0, (durationInFrames - frame) / outFrames))
        : wordProgress;
  } else {
    reveal = typewriterShown(frame, durationInFrames, entrance, exit);
  }
  const { shown, cursorRun } = typewriterSplit(runLengths, reveal);
  const cursorOn = typewriterCursorOn(reveal);
  const motion = {
    ...(cued && entrance !== "typewriter"
      ? wordCuedEntrance(wordProgress, entrance)
      : entranceTransform(frame, durationInFrames, entrance)),
    ...exitTransform(frame, durationInFrames, exit),
  };

  const boxWidth = width - safeArea.left - safeArea.right;
  const boxTop = safeArea.top;
  const boxHeight = height - safeArea.top - safeArea.bottom;

  // Centred in the safe box unless the declaration states a `y`. When it
  // does, the stack's own centre sits on that fraction of the DELIVERY
  // frame - the same frame `timed_text_overlay` normalises against, so
  // one reading of `y` in this engine rather than two.
  const positioned: React.CSSProperties =
    y === undefined || y === null
      ? {
          top: boxTop,
          height: boxHeight,
          justifyContent: "center",
        }
      : {
          top: 0,
          height: height,
          justifyContent: "flex-start",
          paddingTop: Math.max(0, height * y),
          transform: "translateY(-50%)",
        };

  return (
    <AbsoluteFill style={{ backgroundColor: background, fontFamily }}>
      <div
        style={{
          position: "absolute",
          left: safeArea.left,
          width: boxWidth,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: `${RUN_GAP_PX}px`,
          opacity,
          ...positioned,
          ...motion,
        }}
      >
        {/*
          The project's own mark, stacked above the runs in the same
          centred column - the wordmark reading order, not a chosen
          layout. Inside the entrance/exit container, so it arrives and
          leaves with the card, but outside the per-run typewriter
          split: it is a picture, not a glyph, and a reveal that spells
          it out letter by letter would be a declared character drawing
          something it does not mean.

          No default size here, for the reason `channel_bug` states its
          own: a mark drawn at a size the engine chose is the engine
          deciding how loud a client's mark is. A declaration that
          states `image_width` gets exactly that many pixels; one that
          does not is contained to the safe box it is already inside,
          which is mechanics (`fits_in_box`) rather than taste.
        */}
        {image ? (
          <Img
            src={staticFile(image)}
            alt=""
            style={{
              width:
                imageWidth === undefined || imageWidth === null
                  ? undefined
                  : `${Math.round(imageWidth)}px`,
              maxWidth: "100%",
              height: "auto",
              objectFit: "contain",
              display: "block",
            }}
          />
        ) : null}
        {runs.map((run, index) => {
          const visible = run.text.slice(0, shown[index]);
          const showCursor = cursorRun === index;
          if (visible.length === 0 && !showCursor) return null;
          return (
            <div
              key={index}
              style={{
                color: run.colour,
                fontSize: `${run.font_size ?? TYPE_SIZE[run.type_role] ?? TYPE_SIZE.supporting}px`,
                fontWeight: TYPE_WEIGHT[run.type_role] ?? TYPE_WEIGHT.supporting,
                lineHeight: 1.18,
                textAlign: "center",
                width: "100%",
                // Wrapped against the safe box, never the frame: the outer
                // insets belong to the platform's interface.
                overflowWrap: "break-word",
              }}
            >
              {visible}
              {showCursor && (
                <span style={{ opacity: cursorOn ? 1 : 0 }}>|</span>
              )}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
