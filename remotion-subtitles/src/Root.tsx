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
  LucieEndCard,
  lucieEndCardSchema,
  type LucieEndCardProps,
} from "./compositions/LucieEndCard";
import {
  LucieLogoAnimation,
  lucieLogoAnimationSchema,
  type LucieLogoAnimationProps,
} from "./compositions/LucieLogoAnimation";
import {
  TimedTextOverlay,
  timedTextOverlaySchema,
  type TimedTextOverlayProps,
} from "./compositions/TimedTextOverlay";
import {
  FourthWallOverlay,
  fourthWallOverlaySchema,
  type FourthWallOverlayProps,
} from "./compositions/FourthWallOverlay";

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

const calculateFourthWallMetadata: CalculateMetadataFunction<FourthWallOverlayProps> =
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

/**
 * Dynamic metadata calculation for LucieEndCard.
 */
const calculateEndCardMetadata: CalculateMetadataFunction<LucieEndCardProps> =
  async ({ props }) => {
    return {
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    };
  };

/**
 * Dynamic metadata calculation for LucieLogoAnimation.
 */
const calculateLogoMetadata: CalculateMetadataFunction<LucieLogoAnimationProps> =
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
          title: "Post A Day Challenge",
          subtitle: "Day 001 • Getting Started",
          accentColor: "#00D4FF",
          showUpperThird: true,
          showProgress: true,
          showAccents: true,
          timelineProgressStart: 0,
          timelineProgressEnd: 0.2,
          fps: 30,
          width: 1080,
          height: 1920,
          durationInFrames: 150,
        }}
        calculateMetadata={calculateMotionMetadata}
      />
      <Composition
        id="LucieEndCard"
        component={LucieEndCard}
        schema={lucieEndCardSchema}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          headline: "We power what AI knows about you.",
          tagline:
            "Strategic storytelling built for human trust and AI visibility.",
          websiteUrl: "luciecontent.com",
          accentColor: "#FFAA4D",
          bgColor: "#253746",
          fps: 30,
          width: 1080,
          height: 1920,
          durationInFrames: 150,
        }}
        calculateMetadata={calculateEndCardMetadata}
      />
      <Composition
        id="LucieLogoAnimation"
        component={LucieLogoAnimation}
        schema={lucieLogoAnimationSchema}
        durationInFrames={90}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          accentColor: "#FFAA4D",
          style: "full" as const,
          fps: 30,
          width: 1080,
          height: 1920,
          durationInFrames: 90,
        }}
        calculateMetadata={calculateLogoMetadata}
      />
      <Composition
        id="FourthWallOverlay"
        component={FourthWallOverlay}
        schema={fourthWallOverlaySchema}
        durationInFrames={1800}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          moments: [
            {
              text: "Night 1 — Through the 4th Wall",
              color: "#D4A34A",
              fontSize: 48,
              startFrame: 162,
              durationFrames: 60,
              x: 0.5,
              y: 0.15,
              fadeInFrames: 10,
              fadeOutFrames: 10,
              fontWeight: 400,
              textAlign: "center" as const,
              textShadow: "0px 4px 12px rgba(0,0,0,0.6)",
            },
            {
              text: "It's 2:16.",
              color: "#00BFFF",
              fontSize: 42,
              startFrame: 1725,
              durationFrames: 75,
              x: 0.5,
              y: 0.5,
              fadeInFrames: 8,
              fadeOutFrames: 8,
              fontWeight: 400,
              textAlign: "center" as const,
              textShadow: "0px 4px 12px rgba(0,0,0,0.6)",
            },
            {
              text: "Night 1. Attack the day tomorrow.",
              color: "#D4A34A",
              fontSize: 42,
              startFrame: 1770,
              durationFrames: 30,
              x: 0.5,
              y: 0.6,
              fadeInFrames: 8,
              fadeOutFrames: 8,
              fontWeight: 400,
              textAlign: "center" as const,
              textShadow: "0px 4px 12px rgba(0,0,0,0.6)",
            },
          ],
          fontFamily: "'Nanum Pen Script', cursive",
          fps: 30,
          width: 1080,
          height: 1920,
          durationInFrames: 1800,
        }}
        calculateMetadata={calculateFourthWallMetadata}
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
          durationInFrames: 1800,
        }}
        calculateMetadata={calculateTimedTextMetadata}
      />
    </>
  );
};
