/**
 * Syntax highlighting for completed code blocks, loaded on demand. `highlight.js/lib/core` and each grammar are separate
 * dynamic imports, so a page without code never pays for them and a block pays only for its own language.
 *
 * The highlighter returns HTML; it is never injected. `parseHighlighted` turns it into a small tree of text and classed spans
 * that React renders as ordinary elements, and anything unexpected (a tag other than a span) discards the result in favour
 * of plain text. Every failure path returns null: a block is never worse off for having tried.
 */
import type { LanguageFn } from "highlight.js";
import type { HighlightLanguage } from "@/features/sessions/highlight-policy";

export type TokenNode = string | { className: string; children: TokenNode[] };

const grammars: Record<HighlightLanguage, () => Promise<{ default: LanguageFn }>> = {
  javascript: () => import("highlight.js/lib/languages/javascript"),
  typescript: () => import("highlight.js/lib/languages/typescript"),
  python: () => import("highlight.js/lib/languages/python"),
  bash: () => import("highlight.js/lib/languages/bash"),
  json: () => import("highlight.js/lib/languages/json"),
  xml: () => import("highlight.js/lib/languages/xml"),
  css: () => import("highlight.js/lib/languages/css"),
  sql: () => import("highlight.js/lib/languages/sql"),
  yaml: () => import("highlight.js/lib/languages/yaml"),
  go: () => import("highlight.js/lib/languages/go"),
  rust: () => import("highlight.js/lib/languages/rust"),
  java: () => import("highlight.js/lib/languages/java"),
  c: () => import("highlight.js/lib/languages/c"),
  cpp: () => import("highlight.js/lib/languages/cpp"),
  csharp: () => import("highlight.js/lib/languages/csharp"),
  diff: () => import("highlight.js/lib/languages/diff"),
  markdown: () => import("highlight.js/lib/languages/markdown"),
  ruby: () => import("highlight.js/lib/languages/ruby"),
  php: () => import("highlight.js/lib/languages/php"),
  kotlin: () => import("highlight.js/lib/languages/kotlin"),
  swift: () => import("highlight.js/lib/languages/swift"),
  dockerfile: () => import("highlight.js/lib/languages/dockerfile"),
  ini: () => import("highlight.js/lib/languages/ini"),
};

const entities: Record<string, string> = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#x27;": "'", "&#39;": "'" };
const decode = (text: string) => text.replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, (entity) => entities[entity]);

/** Parses the highlighter's output (`text` and `<span class="…">…</span>` only) into a tree, or null for anything else. */
export function parseHighlighted(html: string): TokenNode[] | null {
  const root: TokenNode[] = [];
  const stack: Array<{ className: string; children: TokenNode[] }> = [];
  const tokens = /<span class="([^"<>]*)">|<\/span>|([^<]+)/g;
  let end = 0;
  for (let match = tokens.exec(html); match; match = tokens.exec(html)) {
    if (match.index !== end) return null; // a tag we do not know
    end = tokens.lastIndex;
    const children = stack.length ? stack[stack.length - 1].children : root;
    if (match[2] !== undefined) children.push(decode(match[2]));
    else if (match[1] !== undefined) {
      const node = { className: match[1], children: [] as TokenNode[] };
      children.push(node);
      stack.push(node);
    } else if (!stack.pop()) return null; // a closing tag with nothing open
  }
  return end === html.length && !stack.length ? root : null;
}

type Core = typeof import("highlight.js/lib/core").default;
let core: Promise<Core> | null = null;
const registered = new Set<HighlightLanguage>();
const results = new Map<string, TokenNode[]>();
const RESULT_LIMIT = 64;

/** Highlights `code` as `language`. Null when the library or grammar cannot be loaded, or the output is not understood. */
export async function highlightCode(code: string, language: HighlightLanguage): Promise<TokenNode[] | null> {
  const key = `${language}\u0000${code}`;
  const cached = results.get(key);
  if (cached) return cached;
  try {
    core ??= import("highlight.js/lib/core").then((module) => module.default);
    const hljs = await core;
    if (!registered.has(language)) {
      hljs.registerLanguage(language, (await grammars[language]()).default);
      registered.add(language);
    }
    const nodes = parseHighlighted(hljs.highlight(code, { language, ignoreIllegals: true }).value);
    if (!nodes) return null;
    results.set(key, nodes);
    while (results.size > RESULT_LIMIT) results.delete(results.keys().next().value as string);
    return nodes;
  } catch {
    core = null; // a failed chunk load may be a blip; the next block tries again
    return null;
  }
}

// Token colours use theme-aware utilities (kept here, as literal strings, so Tailwind sees them). Light uses the -700 shades
// and dark the -300 ones, both above 4.5:1 on the code block's background.
const tokenClasses: Array<[scope: string, classes: string]> = [
  ["comment", "text-muted-foreground italic"],
  ["quote", "text-muted-foreground italic"],
  ["doctag", "text-muted-foreground"],
  ["meta", "text-muted-foreground"],
  ["keyword", "text-violet-700 dark:text-violet-300"],
  ["selector-tag", "text-violet-700 dark:text-violet-300"],
  ["literal", "text-amber-700 dark:text-amber-300"],
  ["number", "text-amber-700 dark:text-amber-300"],
  ["string", "text-emerald-700 dark:text-emerald-300"],
  ["regexp", "text-emerald-700 dark:text-emerald-300"],
  ["symbol", "text-emerald-700 dark:text-emerald-300"],
  ["addition", "text-emerald-700 dark:text-emerald-300"],
  ["deletion", "text-red-700 dark:text-red-300"],
  ["built_in", "text-cyan-700 dark:text-cyan-300"],
  ["type", "text-teal-700 dark:text-teal-300"],
  ["title", "text-sky-700 dark:text-sky-300"],
  ["section", "text-sky-700 dark:text-sky-300 font-semibold"],
  ["attr", "text-sky-700 dark:text-sky-300"],
  ["attribute", "text-sky-700 dark:text-sky-300"],
  ["name", "text-sky-700 dark:text-sky-300"],
  ["tag", "text-sky-700 dark:text-sky-300"],
  ["selector-class", "text-sky-700 dark:text-sky-300"],
  ["selector-id", "text-sky-700 dark:text-sky-300"],
  ["variable", "text-orange-700 dark:text-orange-300"],
  ["template-variable", "text-orange-700 dark:text-orange-300"],
  ["emphasis", "italic"],
  ["strong", "font-semibold"],
];

/** The utility classes for a highlighter scope list such as `hljs-title function_`; empty when the scope is not coloured. */
export function tokenClass(scopes: string): string {
  const names = scopes.split(/\s+/).filter((name) => name.startsWith("hljs-")).map((name) => name.slice("hljs-".length));
  for (const [scope, classes] of tokenClasses) if (names.includes(scope)) return classes;
  return "";
}
