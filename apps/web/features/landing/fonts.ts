import localFont from "next/font/local";

/**
 * The public pages' typeface (landing, sign-in, sign-up), self-hosted through Next: a hashed, long-cached file, preloaded only on
 * the pages that render it, with a size-adjusted fallback while it loads. `[data-public]` uses it through `--font-manrope`.
 */
export const manrope = localFont({
  src: "../../public/fonts/manrope.ttf",
  weight: "200 800",
  display: "swap",
  variable: "--font-manrope",
});
