import "./index.css";
import { Composition } from "remotion";
import type { CalculateMetadataFunction } from "remotion";
import {
  SubtitleOverlay,
  subtitleOverlaySchema,
  type SubtitleOverlayProps,
} from "./compositions/SubtitleOverlay";
import {
  MotionGraphics,
  motionGraphicsSchema,
  type MotionGraphicsProps,
} from "./compositions/MotionGraphics";
import {
  TimedTextOverlay,
  timedTextOverlaySchema,
  type TimedTextOverlayProps,
} from "./compositions/TimedTextOverlay";
import {
  FullFrameCard,
  fullFrameCardSchema,
  type FullFrameCardProps,
} from "./compositions/FullFrameCard";
import {
  BrandMotion,
  brandMotionSchema,
  type BrandMotionProps,
} from "./compositions/BrandMotion";
import {
  StagedScene,
  stagedSceneSchema,
  type StagedSceneProps,
} from "./compositions/StagedScene";
// The studio has no pipeline behind it, so its preview defaults need the
// safe area written down somewhere TypeScript can import. This file is
// GENERATED from library/tools/safe_area.py by
// scripts/generate_safe_area_defaults.py, so the studio previews the same
// insets a render gets rather than a second hand-written margin - which
// is what the captain's ruling of 2026-08-25 forbade more of.
import { CAPTION_MAX_WIDTH, SAFE_AREA } from "./safeArea.generated";

/**
 * Dynamic metadata calculation for SubtitleOverlay — sets duration, fps,
 * width, height from props so the pipeline can pass exact dimensions.
 */
const calculateSubtitleMetadata: CalculateMetadataFunction<SubtitleOverlayProps> =
  async ({ props }) => {
    return {
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    };
  };

/**
 * Dynamic metadata calculation for MotionGraphics.
 */
const calculateMotionMetadata: CalculateMetadataFunction<MotionGraphicsProps> =
  async ({ props }) => {
    return {
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    };
  };

const calculateTimedTextMetadata: CalculateMetadataFunction<TimedTextOverlayProps> =
  async ({ props }) => {
    return {
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    };
  };

const calculateFullFrameMetadata: CalculateMetadataFunction<FullFrameCardProps> =
  async ({ props }) => {
    return {
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    };
  };

const calculateStagedSceneMetadata: CalculateMetadataFunction<StagedSceneProps> =
  async ({ props }) => {
    return {
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    };
  };

const calculateBrandMotionMetadata: CalculateMetadataFunction<BrandMotionProps> =
  async ({ props }) => {
    return {
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    };
  };

/**
 * Sample subtitle data for previewing in Remotion Studio.
 */
const sampleSubtitles = [
  {
    text: "and so my very,",
    startFrame: 0,
    endFrame: 21,
    emphasisWords: [] as string[],
    words: [
      { word: "and", startFrame: 0, endFrame: 5 },
      { word: "so", startFrame: 5, endFrame: 9 },
      { word: "my", startFrame: 9, endFrame: 13 },
      { word: "very,", startFrame: 13, endFrame: 21 },
    ],
  },
  {
    text: "very small",
    startFrame: 21,
    endFrame: 42,
    emphasisWords: ["small"],
    words: [
      { word: "very", startFrame: 21, endFrame: 30 },
      { word: "small", startFrame: 30, endFrame: 42 },
    ],
  },
  {
    text: "announcement is",
    startFrame: 42,
    endFrame: 63,
    emphasisWords: ["announcement"],
    words: [
      { word: "announcement", startFrame: 42, endFrame: 54 },
      { word: "is", startFrame: 54, endFrame: 63 },
    ],
  },
  {
    text: "that i just want",
    startFrame: 63,
    endFrame: 84,
    emphasisWords: ["want"],
    words: [
      { word: "that", startFrame: 63, endFrame: 68 },
      { word: "i", startFrame: 68, endFrame: 72 },
      { word: "just", startFrame: 72, endFrame: 77 },
      { word: "want", startFrame: 77, endFrame: 84 },
    ],
  },
  {
    text: "to post every",
    startFrame: 84,
    endFrame: 105,
    emphasisWords: ["post", "every"],
    words: [
      { word: "to", startFrame: 84, endFrame: 89 },
      { word: "post", startFrame: 89, endFrame: 96 },
      { word: "every", startFrame: 96, endFrame: 105 },
    ],
  },
  {
    text: "single day.",
    startFrame: 105,
    endFrame: 126,
    emphasisWords: ["single"],
    words: [
      { word: "single", startFrame: 105, endFrame: 116 },
      { word: "day.", startFrame: 116, endFrame: 126 },
    ],
  },
];

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="SubtitleOverlay"
        component={SubtitleOverlay}
        schema={subtitleOverlaySchema}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          subtitles: sampleSubtitles,
          fps: 30,
          width: 1080,
          height: 1920,
          durationInFrames: 150,
          style: {
            safeArea: SAFE_AREA,
            captionMaxWidth: CAPTION_MAX_WIDTH,
          },
        }}
        calculateMetadata={calculateSubtitleMetadata}
      />
      <Composition
        id="MotionGraphics"
        component={MotionGraphics}
        schema={motionGraphicsSchema}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          // Empty, like TimedTextOverlay's `moments`. A studio default
          // carrying copy and a colour is a title and a palette nobody
          // chose sitting in the repository, and #00D4FF - the cyan
          // that used to be here - is the one colour
          // generate_motion_props keeps a name for precisely so tests
          // can assert it never reaches a frame again.
          elements: [],
          fps: 30,
          width: 1080,
          height: 1920,
          safeArea: SAFE_AREA,
          durationInFrames: 150,
        }}
        calculateMetadata={calculateMotionMetadata}
      />
      <Composition
        id="TimedTextOverlay"
        component={TimedTextOverlay}
        schema={timedTextOverlaySchema}
        durationInFrames={1800}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          moments: [],
          fontFamily: "Helvetica",
          fps: 30,
          width: 1080,
          height: 1920,
          safeArea: SAFE_AREA,
          durationInFrames: 1800,
        }}
        calculateMetadata={calculateTimedTextMetadata}
      />
      <Composition
        id="FullFrameCard"
        component={FullFrameCard}
        schema={fullFrameCardSchema}
        durationInFrames={60}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          // Empty, and BLACK rather than a colour, for the same reason
          // MotionGraphics defaults to no elements: a studio default
          // carrying copy and a ground is a title card and a palette
          // nobody chose sitting in the repository. Black is the absence
          // of a look, not a choice of one (AGENTS.md 10.5).
          runs: [],
          background: "#000000",
          entrance: "cut",
          exit: "cut",
          fontFamily: "Montserrat",
          fps: 30,
          width: 1080,
          height: 1920,
          durationInFrames: 60,
          safeArea: SAFE_AREA,
        }}
        calculateMetadata={calculateFullFrameMetadata}
      />
      <Composition
        id="StagedScene"
        component={StagedScene}
        schema={stagedSceneSchema}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          // No layers, and BLACK for the ground, for the same reason
          // FullFrameCard defaults that way: a studio default carrying a
          // surface, a grain and a move would be a look nobody chose
          // sitting in the repository. Black is the absence of a ground,
          // not a choice of one (AGENTS.md 10.5), and the empty layer
          // list draws nothing over it.
          ground: { colour: "#000000" },
          layers: [],
          fontFamily: "Montserrat",
          fps: 30,
          width: 1080,
          height: 1920,
          durationInFrames: 150,
        }}
        calculateMetadata={calculateStagedSceneMetadata}
      />
      <Composition
        id="BrandMotion"
        component={BrandMotion}
        schema={brandMotionSchema}
        durationInFrames={45}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          // Empty, for the same reason MotionGraphics defaults to no
          // elements: a studio default naming a file would be artwork
          // nobody chose sitting in the repository, and the component
          // renders null on an empty src rather than throwing. `muted`
          // is true here because a studio preview is not a render - the
          // pipeline still requires it stated on every real props file.
          src: "",
          fps: 30,
          width: 1080,
          height: 1920,
          durationInFrames: 45,
          muted: true,
        }}
        calculateMetadata={calculateBrandMotionMetadata}
      />
    </>
  );
};
