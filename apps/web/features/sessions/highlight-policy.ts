/**
 * Which code blocks get syntax highlighting. Highlighting is a nicety, never a requirement: a block that is still being
 * written, is too large, or names a language we do not bundle stays plain text. Free of the highlighter itself, so the rules
 * are node-tested and the heavy library is only imported once a block qualifies.
 */

/** Blocks longer than this stay plain: highlighting is linear, but a huge block should not cost a long main-thread task. */
export const HIGHLIGHT_MAX_CHARS = 20_000;

/** The languages with a bundled grammar (each is its own lazily loaded chunk). */
export const highlightLanguages = [
  "javascript", "typescript", "python", "bash", "json", "xml", "css", "sql", "yaml", "go", "rust", "java", "c", "cpp", "csharp",
  "diff", "markdown", "ruby", "php", "kotlin", "swift", "dockerfile", "ini",
] as const;

export type HighlightLanguage = (typeof highlightLanguages)[number];

const aliases: Record<string, HighlightLanguage> = {
  js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript", node: "javascript",
  ts: "typescript", tsx: "typescript", mts: "typescript", cts: "typescript",
  py: "python", python3: "python", py3: "python",
  sh: "bash", shell: "bash", zsh: "bash", console: "bash", shellscript: "bash",
  jsonc: "json", json5: "json",
  html: "xml", htm: "xml", svg: "xml", xhtml: "xml",
  yml: "yaml",
  golang: "go",
  rs: "rust",
  "c++": "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp", h: "c",
  "c#": "csharp", cs: "csharp",
  patch: "diff",
  md: "markdown",
  rb: "ruby",
  kt: "kotlin",
  docker: "dockerfile",
  toml: "ini",
};

const known = new Set<string>(highlightLanguages);

/** The bundled grammar for a fence's language label ("TSX", "c++", "sh"), or null when there is none. */
export function canonicalLanguage(label: string | null | undefined): HighlightLanguage | null {
  if (!label) return null;
  const name = label.trim().toLowerCase();
  if (known.has(name)) return name as HighlightLanguage;
  return Object.hasOwn(aliases, name) ? aliases[name] : null; // not `aliases[name]`: "constructor" is not a language
}

export type Eligibility =
  | { ok: true; language: HighlightLanguage }
  | { ok: false; reason: "streaming" | "empty" | "too_large" | "unsupported" };

/** Whether a block should be highlighted, and with which grammar. Only completed (non-streaming) blocks qualify. */
export function highlightEligibility({ code, language, streaming }: { code: string; language: string | null | undefined; streaming: boolean }): Eligibility {
  if (streaming) return { ok: false, reason: "streaming" };
  if (!code.trim()) return { ok: false, reason: "empty" };
  if (code.length > HIGHLIGHT_MAX_CHARS) return { ok: false, reason: "too_large" };
  const canonical = canonicalLanguage(language);
  return canonical ? { ok: true, language: canonical } : { ok: false, reason: "unsupported" };
}
