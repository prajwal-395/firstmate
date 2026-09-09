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
import {
  AbsoluteFill,
  Easing,
  Img,
  staticFile,
  useCurrentFrame,
} from "remotion";
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
 * EFFECTIVE start (`start` minus `lead`) has passed, so a cue boundary
 * always coincides with a word boundary on screen. Planned by
 * `library/tools/full_frame_element.py` (`word_sync`), which refuses
 * the segment rather than emitting cues its runs cannot match.
 *
 * `lead` is the seconds the cue's REVEAL leads its spoken onset -
 * blockers 4 of docs/SPAN_RENDERER_CAPABILITY.md, measured in
 * docs/ANIMATION_FIRST_REFERENCE.md section 1 (each word fades up
 * starting ~100 ms before its onset so it reaches full opacity ON the
 * syllable). Absent means 0, which is the absence of anticipation
 * rather than a chosen timing: the reveal steps on the onset, as
 * before. Declared per cue because the reference's own leads span a
 * wide range (67-839 ms) - one number for the whole card cannot state
 * them. The speech window itself never moves: emphasis still reads the
 * measured [start, end), so leading the reveal cannot move the
 * highlight onto a word nobody is speaking.
 */
export type WordCue = {
  word: string;
  start: number;
  end: number;
  chars: number;
  /** Seconds this cue's reveal leads its onset. Absent means 0. */
  lead?: number;
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
  /**
   * The colour the CURRENT word draws in while it owns the clock.
   * Declared or absent, never defaulted: the engine ships no palette,
   * so an undeclared emphasis draws nothing in a second colour - every
   * word keeps its run's colour. Read only beside `wordCues` on a
   * per-word drawing (`mask`, `draw`); the plan refuses it wherever no
   * drawing reads it.
   */
  emphasisColour?: string;
  /**
   * Render every Nth frame twice - motion on twos, threes, and so on.
   *
   * Blocker 8 of docs/SPAN_RENDERER_CAPABILITY.md, measured in
   * docs/ANIMATION_FIRST_REFERENCE.md section 5 (51% of the
   * reference's consecutive frame pairs identical: 15 fps inside a 30
   * fps container). The same function shape StagedScene already draws
   * (`heldFrame` there). Absent or 1 draws every frame, which is the
   * absence of stylisation rather than a chosen cadence - and what
   * keeps props written before this slot existed rendering
   * byte-identically: no key, no change of path. The card carries no
   * audio (a span keeps spine audio on its own clock), so holding the
   * picture holds nothing else.
   */
  holdFrames?: number;
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

/** The frame a quantised card draws.
 *
 * `holdFrames` of 2 renders every second frame twice - motion on twos,
 * which is 51% of the reference's frame pairs
 * (docs/ANIMATION_FIRST_REFERENCE.md section 5). Absent or 1 draws
 * every frame. Exported because the card's behaviour over time is the
 * thing worth asserting in a test without rendering a frame.
 *
 * A hold that is not a positive integer is malformed, not a softer
 * hold: 0, a negative, or a fraction REFUSES rather than being
 * clamped to the nearest thing that draws.
 */
export const heldFrame = (frame: number, holdFrames?: number): number => {
  if (holdFrames === undefined || holdFrames === null) return frame;
  if (
    typeof holdFrames !== "number" ||
    !Number.isInteger(holdFrames) ||
    holdFrames < 1
  ) {
    throw new Error(
      `FullFrameCard holdFrames=${String(holdFrames)} is not a positive ` +
        `integer. A frame hold quantises the clock to whole held frames; ` +
        `omit it and every frame is drawn.`,
    );
  }
  return holdFrames > 1
    ? Math.floor(frame / holdFrames) * holdFrames
    : frame;
};

/** The declared lead of one cue, in seconds. Absent means 0. */
export const cueLead = (cue: WordCue): number =>
  cue.lead === undefined || cue.lead === null ? 0 : cue.lead;

/** The second one cue's reveal lands on: its onset, led by its lead. */
export const cueEffectiveStart = (cue: WordCue): number =>
  cue.start - cueLead(cue);

/** Refuse a lead that moves a cue off its own word, naming the cue.
 *
 * The reveal step must land in the gap between the previous word's end
 * and this word's onset (or between zero and the onset for the first
 * word). A lead large enough to push the step onto the previous word
 * would silently mistime the caption, so it throws rather than
 * clamping - the plan side states the lead and the renderer refuses
 * the one it cannot place. A negative lead is a delay, not a lead,
 * and is refused for the same reason in the other direction.
 *
 * The 1e-9 tolerance is floating-point hygiene (both ends are
 * int/fps quotients), not a widened gate: it is three orders of
 * magnitude below a frame at any delivery rate.
 */
export const validateCueLeads = (cues: WordCue[]): void => {
  if (!cues || cues.length === 0) return;
  const ordered = [...cues].sort((a, b) => a.start - b.start);
  for (let i = 0; i < ordered.length; i += 1) {
    const lead = cueLead(ordered[i]);
    if (typeof lead !== "number" || !Number.isFinite(lead) || lead < 0) {
      throw new Error(
        `FullFrameCard cue ${i} (${ordered[i].word}) carries lead=${String(
          ordered[i].lead,
        )}. A lead is a non-negative number of seconds the reveal ` +
          `anticipates its onset; omit it and the reveal lands on the onset.`,
      );
    }
    const floor = i === 0 ? 0 : ordered[i - 1].end;
    const effective = ordered[i].start - lead;
    if (effective < floor - 1e-9) {
      throw new Error(
        `FullFrameCard cue ${i} (${ordered[i].word}) leads by ${lead}s ` +
          `to ${effective.toFixed(3)}s, inside the previous word's window ` +
          `(floor ${floor.toFixed(3)}s). A lead that pushes a cue off its ` +
          `own word mistimes the caption; declare a shorter lead.`,
      );
    }
  }
};

/** Characters shown at `frame` off the word clock.
 *
 * The last cue whose EFFECTIVE start has passed owns the frame -
 * karaoke reading, not interpolation, so the reveal steps exactly on
 * (led) word starts and holds between them. A cue with no lead reads
 * its onset, which is the path above unchanged. Clamped to
 * `totalChars`: cues count over the resolved runs, so anything past
 * the end is a plan defect contained here rather than drawn past the
 * text.
 */
export const wordCuedChars = (
  frame: number,
  fps: number,
  cues: WordCue[],
  totalChars: number,
): number => {
  if (!cues || cues.length === 0 || totalChars <= 0) return 0;
  const t = fps > 0 ? frame / fps : 0;
  const ordered = [...cues].sort(
    (a, b) => cueEffectiveStart(a) - cueEffectiveStart(b),
  );
  let shown = 0;
  for (const cue of ordered) {
    if (t >= cueEffectiveStart(cue))
      shown = Math.min(cue.chars, totalChars);
  }
  return Math.max(0, shown);
};

/** One word on the word clock, resolved against the runs it is drawn from.
 *
 * `runIndex` is which run draws the word, so the word keeps its run's
 * colour, weight and size. Offsets are character offsets into the runs'
 * concatenation - the same string `_word_cues` counts `chars` over - so
 * this split and the plan's cues agree word for word.
 */
export type CuedWord = {
  text: string;
  runIndex: number;
  start: number;
  end: number;
  /** The cue's declared lead, resolved (absent means 0). */
  lead: number;
};

/** Split the runs' concatenation into the words the cues pace, in order.
 *
 * Returns null when the words on screen are not the words the clock
 * carries - a cue boundary must coincide with a word boundary, and a
 * mismatch here means the plan's refusal did not run. The caller falls
 * back to the block-level wipe rather than landing near words instead
 * of on them.
 */
export const splitCuedWords = (
  runs: CardRun[],
  cues: WordCue[],
): CuedWord[] | null => {
  const texts = runs.map((run) => run.text);
  const joined = texts.join("");
  const words: { text: string; at: number }[] = [];
  const pattern = /\S+/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(joined)) !== null) {
    words.push({ text: match[0], at: match.index });
  }
  if (words.length !== cues.length) return null;
  const ordered = [...cues].sort((a, b) => a.start - b.start);
  const out: CuedWord[] = [];
  for (let i = 0; i < words.length; i += 1) {
    if (words[i].text.toLowerCase() !== ordered[i].word.toLowerCase()) {
      return null;
    }
    let runIndex = texts.length - 1;
    let cursor = 0;
    for (let r = 0; r < texts.length; r += 1) {
      cursor += texts[r].length;
      if (words[i].at < cursor) {
        runIndex = r;
        break;
      }
    }
    out.push({
      text: words[i].text,
      runIndex,
      start: ordered[i].start,
      end: ordered[i].end,
      lead: cueLead(ordered[i]),
    });
  }
  return out;
};

/** How far a word has risen at `frame`, 0 (hidden in its mask) to 1.
 *
 * The window is the word's own measured speech - cue start to cue end -
 * LED by the cue's lead, so the rise keeps its choreographed shape and
 * lands earlier by exactly the lead rather than starting there. A lead
 * of 0 reads the measured window unchanged. A cue with no measured
 * duration still owns its frame: with nothing to ramp across, the word
 * lands instead of never arriving.
 * The easing is the reference catalogue's, not a chosen curve:
 * remotion-bits `AnimatedText` paces its per-word y/blur/opacity stagger
 * with `easeOutCubic` (the captain's reference link, Blur In and Word by
 * Word pages), and GSAP's SplitText text-masking demo rises each masked
 * line on the same ease-out shape.
 */
export const cuedWordProgress = (
  frame: number,
  fps: number,
  word: CuedWord,
): number => {
  const lead = word.lead === undefined || word.lead === null ? 0 : word.lead;
  const startF = (word.start - lead) * fps;
  const endF = (word.end - lead) * fps;
  if (!(endF > startF)) return frame >= startF ? 1 : 0;
  const p = (frame - startF) / (endF - startF);
  const clamped = Math.min(1, Math.max(0, p));
  return Easing.out(Easing.cubic)(clamped);
};

/** Which cue owns the clock at `frame`, or -1 when none does.
 *
 * The current word is the one whose measured speech window contains
 * this frame - cue start inclusive, cue end exclusive. Between words,
 * before the first and past the last, nothing is current: emphasis
 * marks the word being spoken, not the last one that was. Deliberately
 * unled: a cue's `lead` anticipates the REVEAL, while the highlight
 * answers "who is speaking now" - leading it would restyle a word
 * nobody is speaking.
 */
export const cuedEmphasisIndex = (
  frame: number,
  fps: number,
  cues: WordCue[],
): number => {
  if (!cues || cues.length === 0 || !(fps > 0)) return -1;
  const t = frame / fps;
  const ordered = [...cues].sort((a, b) => a.start - b.start);
  for (let i = 0; i < ordered.length; i += 1) {
    if (t >= ordered[i].start && t < ordered[i].end) return i;
  }
  return -1;
};

/** How far below its rest line a word at progress `p` sits, in percent.
 *
 * 120 rather than 100: the mask wrapper carries descender clearance
 * below the line box, and 100% would leave the word's top edge sitting
 * in that clearance - a sliver of glyph before the word is spoken.
 * 120 clears the line box plus the clearance, so unspoken is invisible.
 */
export const cuedWordRisePercent = (progress: number): number =>
  (1 - Math.min(1, Math.max(0, progress))) * 120;

/** The entrance transform at word progress `p`, 0..1, for `draw`.
 *
 * The same drawing `entranceTransform` in MotionGraphics gives - the
 * scale 0.85 to 1 and blur 4px to 0, not respelled - with a progress
 * value substituted for the ramp clock: one drawing of the character,
 * two clocks. Read once per WORD off that word's own speech window by
 * the per-word path below, and once per CARD off the whole clock by
 * the block-level fallback, which is what keeps a cue mismatch drawing
 * the wipe rather than nothing. `typewriter` needs none: it is a
 * reveal, not a transform, and the split below already reads the cues.
 * `mask` needs none either: a cued mask rises each word out of its own
 * mask (`cuedWordProgress`), which a block-level wipe cannot draw.
 *
 * A word this transform reads at progress 0 is unspoken: blur alone
 * does not hide it, so the per-word path pairs this with zero opacity
 * there. The numbers stay the character's; the hiding is the clock's.
 */
export const wordCuedEntrance = (
  progress: number,
  entrance: string,
): React.CSSProperties => {
  const p = Math.min(1, Math.max(0, progress));
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
  emphasisColour,
  holdFrames,
}) => {
  // The card's whole clock goes through the hold: entrances, exits,
  // the word clock and emphasis all read `frame`, so motion on twos
  // steps everything together - the way StagedScene derives its `t`
  // from its held frame. Absent or 1 is the identity, so props
  // written before this slot existed draw exactly as before; anything
  // else that is not a positive integer refuses inside `heldFrame`.
  const frame = heldFrame(useCurrentFrame(), holdFrames);

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
  // Refused where read, never clamped: a lead that pushes a cue off
  // its own word and onto the previous one would silently mistime the
  // caption. Cues beside an uncued entrance are not read at all, so
  // they are not validated either - props that ignore their cues draw
  // exactly as before.
  if (cued) validateCueLeads(wordCues as WordCue[]);
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
  // A cued `mask` rises each word out of its own mask, and a cued
  // `draw` resolves each word out of its own blur: the SplitText
  // line-masking look and the blur-resolve look, each paced by the word
  // clock. Every other cued character keeps its block-level drawing,
  // and a word list the cues do not match keeps the wipe - the
  // mismatch means the plan's refusal did not run, and landing near
  // words is not landing on them.
  const perWord = cued && (entrance === "mask" || entrance === "draw");
  const cuedWords = perWord
    ? splitCuedWords(runs, wordCues as WordCue[])
    : null;
  // The current word, when the declaration states an emphasis colour.
  // Absent beside absent: no key, no restyle, words keep their run's
  // colour rather than a fallback nobody chose.
  const emphasisIndex =
    cuedWords && emphasisColour
      ? cuedEmphasisIndex(frame, fps, wordCues as WordCue[])
      : -1;
  const motion = {
    ...(cuedWords
      ? {}
      : cued && entrance !== "typewriter"
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
        {cuedWords ? (
          <div
            style={{
              color: runs[cuedWords[0]?.runIndex ?? 0]?.colour,
              fontSize: `${runs[cuedWords[0]?.runIndex ?? 0]?.font_size ?? TYPE_SIZE[runs[cuedWords[0]?.runIndex ?? 0]?.type_role] ?? TYPE_SIZE.supporting}px`,
              fontWeight:
                TYPE_WEIGHT[runs[cuedWords[0]?.runIndex ?? 0]?.type_role] ??
                TYPE_WEIGHT.supporting,
              lineHeight: 1.18,
              textAlign: "center",
              width: "100%",
              overflowWrap: "break-word",
            }}
          >
            {cuedWords.map((word, index) => {
              const run = runs[word.runIndex] ?? runs[0];
              const progress = cuedWordProgress(frame, fps, word);
              // The current word draws in the declared emphasis colour;
              // every other word keeps its run's. The clock paces the
              // words and, when stated, restyles the one it owns - it
              // never restyles what it does not own.
              const colour =
                index === emphasisIndex && emphasisColour
                  ? emphasisColour
                  : run.colour;
              if (entrance === "draw") {
                return (
                  <React.Fragment key={index}>
                    {index > 0 ? " " : null}
                    {/*
                      One resolving word per cue: the `draw` drawing at
                      the word's own progress, invisible while unspoken.
                      Blur alone does not hide, so progress 0 pairs the
                      transform with zero opacity - the hiding is the
                      clock's, the look is the character's.
                    */}
                    <span
                      style={{
                        display: "inline-block",
                        verticalAlign: "bottom",
                        color: colour,
                        fontSize: `${run.font_size ?? TYPE_SIZE[run.type_role] ?? TYPE_SIZE.supporting}px`,
                        fontWeight:
                          TYPE_WEIGHT[run.type_role] ?? TYPE_WEIGHT.supporting,
                        opacity: progress <= 0 ? 0 : progress,
                        ...wordCuedEntrance(progress, "draw"),
                      }}
                    >
                      {word.text}
                    </span>
                  </React.Fragment>
                );
              }
              const rise = cuedWordRisePercent(progress);
              return (
                <React.Fragment key={index}>
                  {index > 0 ? " " : null}
                  {/*
                    One masked line per word, in the GSAP SplitText
                    shape: the outer span is the mask (overflow hidden),
                    the inner one rises from below it to its rest line.
                    The bottom clearance keeps descenders inside the
                    mask at rest - without it the mask clips every
                    descending glyph - and the rise clears the line box
                    plus that clearance, so unspoken is invisible.
                    Colour, weight and size stay the run's: the clock
                    paces the words, it does not restyle them - except
                    the current word, which takes the declared emphasis
                    colour above.
                  */}
                  <span
                    style={{
                      display: "inline-block",
                      overflow: "hidden",
                      verticalAlign: "bottom",
                      paddingBottom: "0.18em",
                      marginBottom: "-0.18em",
                      color: colour,
                      fontSize: `${run.font_size ?? TYPE_SIZE[run.type_role] ?? TYPE_SIZE.supporting}px`,
                      fontWeight:
                        TYPE_WEIGHT[run.type_role] ?? TYPE_WEIGHT.supporting,
                    }}
                  >
                    <span
                      style={{
                        display: "inline-block",
                        transform: `translateY(${rise}%)`,
                      }}
                    >
                      {word.text}
                    </span>
                  </span>
                </React.Fragment>
              );
            })}
          </div>
        ) : (
          runs.map((run, index) => {
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
          })
        )}
      </div>
    </AbsoluteFill>
  );
};
