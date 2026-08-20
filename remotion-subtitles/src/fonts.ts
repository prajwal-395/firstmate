import { continueRender, delayRender, staticFile } from "remotion";

/**
 * Fonts, loaded from disk and BLOCKING the render until they are ready.
 *
 * P3.3. `src/index.css` used to do
 *
 *     @import url('https://fonts.googleapis.com/css2?family=Montserrat...')
 *
 * with no `delayRender`, so whether the correct font was in place when
 * Remotion started rasterising was a race with the network. On a miss the
 * captions rendered in Chromium's fallback sans at a different width,
 * which changes line breaking as well as the letterforms - and nothing
 * downstream could tell, because the frames were still valid PNGs of the
 * right size.
 *
 * Montserrat now ships in `public/fonts/` as a single variable file
 * covering the whole 100-900 weight axis, which is every weight
 * `library/tools/subtitle_style.py` can ask for. No network at render
 * time, and `delayRender` holds the first frame until the face is
 * genuinely usable rather than merely requested.
 *
 * Licence: SIL Open Font License 1.1, redistributable including
 * commercially. The text ships beside the font as
 * `public/fonts/OFL-Montserrat.txt`; see AGENTS.md section 11.
 */

export const BUNDLED_FONT_FAMILY = "Montserrat";
export const BUNDLED_FONT_FILE = "fonts/Montserrat-Variable.ttf";

let loaded: Promise<void> | null = null;

/**
 * Load the bundled fonts once per render process.
 *
 * Safe to call from several compositions: the promise is memoised, so the
 * font is registered once and every later caller awaits the same load.
 */
export const loadBundledFonts = (): Promise<void> => {
  if (loaded) {
    return loaded;
  }

  const handle = delayRender(
    `Loading bundled font ${BUNDLED_FONT_FAMILY} from ${BUNDLED_FONT_FILE}`,
  );

  loaded = (async () => {
    const face = new FontFace(
      BUNDLED_FONT_FAMILY,
      `url(${staticFile(BUNDLED_FONT_FILE)}) format('truetype')`,
      // The variable axis, so a single file serves every weight a brand
      // template can name. Without this Chromium synthesises bold, which
      // is not the same shape as the real 900 weight.
      { weight: "100 900", style: "normal" },
    );
    await face.load();
    document.fonts.add(face);
    // `load()` resolves when the bytes are parsed; this waits until the
    // face is actually usable for layout, which is the thing that used to
    // race.
    await document.fonts.ready;
  })()
    .catch((err) => {
      // Fail loudly. A silent fallback to Chromium's default sans is the
      // exact failure this module exists to remove, and it is invisible
      // in the output.
      throw new Error(
        `Failed to load bundled font ${BUNDLED_FONT_FAMILY} from ` +
          `${BUNDLED_FONT_FILE}: ${err instanceof Error ? err.message : String(err)}`,
      );
    })
    .finally(() => {
      continueRender(handle);
    });

  return loaded;
};

/**
 * Load a font the PROJECT carries, blocking the render on it.
 *
 * Per-series typefaces live with the project that owns the series and
 * never in this repository (captain's ruling, 2026-08-20), so the file
 * arrives at render time: `remotion_brand_linker.prep_remotion` copies
 * `<project>/brand_assets/*` into `public/brand/`, and the declaration
 * names it with `font_file`.
 *
 * It throws on a miss for the same reason `loadBundledFonts` does, and
 * the reason is sharper here: nobody has ever seen this face rendered by
 * this pipeline, so a silent substitution looks exactly like the card
 * working. See library/tools/render_fonts.py.
 */
const projectFonts = new Map<string, Promise<void>>();

export const loadProjectFont = (
  family: string,
  file: string,
): Promise<void> => {
  const key = `${family}|${file}`;
  const existing = projectFonts.get(key);
  if (existing) {
    return existing;
  }

  const handle = delayRender(`Loading project font ${family} from ${file}`);

  const loading = (async () => {
    const face = new FontFace(family, `url(${staticFile(file)})`);
    await face.load();
    document.fonts.add(face);
    await document.fonts.ready;
  })()
    .catch((err) => {
      throw new Error(
        `Failed to load project font ${family} from ${file}: ` +
          `${err instanceof Error ? err.message : String(err)}. ` +
          `A per-series typeface is staged from <project>/brand_assets/ ` +
          `into public/brand/ by prep_remotion; without it the card ` +
          `renders in Chromium's fallback sans and nothing downstream ` +
          `can tell.`,
      );
    })
    .finally(() => {
      continueRender(handle);
    });

  projectFonts.set(key, loading);
  return loading;
};
