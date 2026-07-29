/**
 * Remotion configuration for transparent ProRes 4444 rendering.
 * Used for subtitle overlays that will be composited in DaVinci Resolve.
 */

import { Config } from "@remotion/cli/config";
import { enableTailwind } from '@remotion/tailwind-v4';

Config.setVideoImageFormat("png");
Config.setPixelFormat("yuva444p10le");
Config.setCodec("prores");
Config.setProResProfile("4444");
Config.setOverwriteOutput(true);
Config.overrideWebpackConfig(enableTailwind);
