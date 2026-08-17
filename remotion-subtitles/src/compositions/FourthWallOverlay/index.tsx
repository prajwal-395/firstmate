/**
 * FourthWallOverlay - thin alias for TimedTextOverlay.
 *
 * The 4th Wall series' text moments are declared in the brand template
 * (library/templates/fourth_wall.yaml), not hardcoded here. This file
 * exists so the Remotion composition ID "FourthWallOverlay" in Root.tsx
 * keeps working without a rename.
 */
export {
  TimedTextOverlay as FourthWallOverlay,
  timedTextOverlaySchema as fourthWallOverlaySchema,
  type TimedTextOverlayProps as FourthWallOverlayProps,
} from "../TimedTextOverlay";
