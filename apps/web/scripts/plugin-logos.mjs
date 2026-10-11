#!/usr/bin/env node
/**
 * Fetches the logo of every plugin in the catalog into `public/plugins/` and writes `lib/plugin-logos.ts`, which says how to draw
 * each one. Run it again when a plugin is added to `apps/api/src/agentforge/plugins/catalog.py`:
 *
 *     node scripts/plugin-logos.mjs
 *
 * Where each logo comes from (all pinned, so the result is reproducible):
 *   - "si"     Simple Icons (CC0), a single-colour mark. It is given the brand's own colour from the set's data file.
 *   - "lobe"   LobeHub icons (MIT), the colour variant of a mark.
 *   - "url"    The logo or app icon the vendor itself publishes (their favicon / apple-touch icon, or the project's own repository).
 * The marks are trademarks of their owners, shown only to name the service they belong to (see docs/plugins.md).
 */
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = path.join(root, "public/plugins");
const SIMPLE_ICONS = "16.35.0";
const LOBE_ICONS = "1.95.1";
const si = (slug) => ({ from: "si", slug });
const lobe = (name) => ({ from: "lobe", name });
const url = (href, ext) => ({ from: "url", href, ext });

const LOGOS = {
  github: si("github"), linear: si("linear"), sentry: si("sentry"), gitlab: si("gitlab"), buildkite: si("buildkite"),
  deepwiki: url("https://t0.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://deepwiki.com&size=128", "png"),
  context7: url("https://raw.githubusercontent.com/upstash/context7/master/public/context7-icon.svg", "svg"),
  "microsoft-learn": lobe("microsoft-color"),
  "cloudflare-docs": si("cloudflare"), "cloudflare-observability": si("cloudflare"),
  "hugging-face": si("huggingface"), netlify: si("netlify"), railway: si("railway"),
  heroku: url("https://www.heroku.com/wp-content/uploads/2025/01/apple-touch-icon-180x180-1.png?w=180", "png"),
  clerk: si("clerk"),
  workos: url("https://cdn.prod.website-files.com/621f54116cab10f6e9215d8b/621f549024eb2947f6f2f7f7_Favicon%20256x256.png", "png"),
  supabase: si("supabase"), neon: si("neon"), planetscale: si("planetscale"), mongodb: si("mongodb"), prisma: si("prisma"),
  datadog: si("datadog"), grafana: si("grafana"), posthog: si("posthog"), mixpanel: si("mixpanel"),
  amplitude: url("https://amplitude.com/nextjs-public/favicon/apple-touch-icon.png", "png"),
  axiom: url("https://axiom.co/apple-touch-icon.png", "png"),
  notion: si("notion"),
  monday: url("https://cdn.prod.website-files.com/656da6fea306219773d04208/65af6bd6e742d497b5f23f69_645898132bbaac20f1963919_256x256.png", "png"),
  clickup: si("clickup"), todoist: si("todoist"), airtable: si("airtable"), miro: si("miro"),
  canva: url("https://static.canva.com/domain-assets/canva/static/images/apple-touch-120x120-1.png", "png"),
  webflow: si("webflow"), wix: si("wix"), cloudinary: si("cloudinary"), sanity: si("sanity"), contentful: si("contentful"),
  stripe: si("stripe"), paypal: si("paypal"),
  ramp: url("https://ramp.com/apple-touch-icon.png", "png"),
  exa: lobe("exa-color"), tavily: lobe("tavily-color"),
  apify: url("https://apify.com/icon0.svg", "svg"),
  parallel: url("https://parallel.ai/icon.svg", "svg"),
  mapbox: si("mapbox"), zapier: si("zapier"),
};

async function get(href) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const response = await fetch(href, { headers: { "user-agent": "Mozilla/5.0 (agentforge plugin-logos)" }, redirect: "follow" });
    if (response.ok) return Buffer.from(await response.arrayBuffer());
    if (response.status !== 429 && response.status < 500) throw new Error(`${href}: HTTP ${response.status}`);
    await new Promise((resolve) => setTimeout(resolve, 1500 * attempt));
  }
  throw new Error(`${href}: gave up`);
}

/** WCAG relative luminance of "RRGGBB". */
function luminance(hex) {
  const channel = (value) => { const v = value / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const [r, g, b] = [0, 2, 4].map((index) => channel(parseInt(hex.slice(index, index + 2), 16)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** An SVG made safe and tidy to serve: no script, no external references, no fixed size, no title tooltip. */
function tidy(svg, fill) {
  let text = svg.toString("utf8");
  if (/<script|javascript:|onload=|onerror=|xlink:href=["']https?:|<image|<foreignObject/i.test(text)) throw new Error("an SVG carries something it should not");
  text = text.replace(/<title>.*?<\/title>/gis, "").replace(/<\?xml[^>]*\?>/g, "").replace(/\s(width|height|style|role)="[^"]*"/g, "");
  if (fill) text = text.replace("<svg", `<svg fill="#${fill}"`);
  return Buffer.from(text.trim() + "\n");
}

await mkdir(out, { recursive: true });
const icons = JSON.parse((await get(`https://cdn.jsdelivr.net/npm/simple-icons@${SIMPLE_ICONS}/data/simple-icons.json`)).toString());
const manifest = {};
for (const [key, source] of Object.entries(LOGOS)) {
  let file, fit, dark = false;
  if (source.from === "si") {
    const entry = icons.find((item) => (item.slug ?? item.title.toLowerCase().replace(/[^a-z0-9]/g, "")) === source.slug);
    if (!entry) throw new Error(`${key}: no Simple Icons entry ${source.slug}`);
    await writeFile(path.join(out, `${key}.svg`), tidy(await get(`https://cdn.jsdelivr.net/npm/simple-icons@${SIMPLE_ICONS}/icons/${source.slug}.svg`), entry.hex));
    // A mark this dark has too little contrast on a dark tile: it is shown white there. Saturated brand colours keep their colour.
    file = `${key}.svg`; fit = "glyph"; dark = luminance(entry.hex) < 0.1;
  } else if (source.from === "lobe") {
    await writeFile(path.join(out, `${key}.svg`), tidy(await get(`https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@${LOBE_ICONS}/icons/${source.name}.svg`)));
    file = `${key}.svg`; fit = "glyph";
  } else {
    const bytes = await get(source.href);
    if (source.ext === "svg") { await writeFile(path.join(out, `${key}.svg`), tidy(bytes)); file = `${key}.svg`; fit = "glyph"; }
    else { await writeFile(path.join(out, `${key}.png`), bytes); file = `${key}.png`; fit = "icon"; }
  }
  manifest[key] = { file, fit, ...(dark ? { dark: true } : {}) };
  console.log(`${key.padEnd(26)} ${source.from.padEnd(5)} ${file}${dark ? "  (white in dark mode)" : ""}`);
}
const lines = Object.entries(manifest).map(([key, value]) => `  ${JSON.stringify(key)}: ${JSON.stringify(value)},`);
await writeFile(path.join(root, "lib/plugin-logos.ts"), `// Generated by scripts/plugin-logos.mjs: do not edit. How each catalog plugin's logo is drawn.
// "glyph" is a mark drawn on a plain tile; "icon" is an app icon that fills it. "dark" marks a near-black mark, shown white in dark mode.
export type PluginLogo = { file: string; fit: "glyph" | "icon"; dark?: true };

export const pluginLogos: Readonly<Record<string, PluginLogo>> = {
${lines.join("\n")}
};
`);
console.log(`\n${Object.keys(manifest).length} logos written to public/plugins and lib/plugin-logos.ts`);
