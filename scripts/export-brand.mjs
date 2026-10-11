/** Deterministic distribution sizes from the approved generated master. Run: node scripts/export-brand.mjs. */
import { createRequire } from "node:module";
import { realpathSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(realpathSync(join(root, "apps/web/node_modules/next/package.json")));
const sharp = require("sharp");
const source = join(root, "assets/brand/agentforge-master.png");
const brand = join(root, "apps/web/public/brand");
const app = join(root, "apps/web/app");
await mkdir(brand, { recursive: true });
const png = (size) => sharp(source).resize(size, size, { kernel: "lanczos3" }).png({ compressionLevel: 9 }).toBuffer();
for (const size of [64, 128, 192, 512]) {
  await writeFile(join(brand, `agentforge-icon-${size}.png`), await png(size));
}
await writeFile(join(app, "icon.png"), await png(64));
await sharp(source).resize(180, 180).flatten({ background: "#101112" })
  .png({ compressionLevel: 9 }).toFile(join(app, "apple-icon.png"));

// ICO supports PNG payloads. Include real 16/32/48 pixel representations for browser/OS selection.
const sizes = [16, 32, 48];
const images = await Promise.all(sizes.map(png));
const header = Buffer.alloc(6 + sizes.length * 16);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(sizes.length, 4);
let offset = header.length;
for (let i = 0; i < sizes.length; i++) {
  const entry = 6 + i * 16;
  header[entry] = header[entry + 1] = sizes[i];
  header.writeUInt16LE(1, entry + 4);
  header.writeUInt16LE(32, entry + 6);
  header.writeUInt32LE(images[i].length, entry + 8);
  header.writeUInt32LE(offset, entry + 12);
  offset += images[i].length;
}
await writeFile(join(app, "favicon.ico"), Buffer.concat([header, ...images]));
console.log("Brand icons exported: browser, Apple, UI and app-manifest sizes.");
