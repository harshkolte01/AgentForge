/**
 * Makes a growing Markdown prefix parse sanely while a model is still writing it.
 *
 * A streamed answer is cut at arbitrary points: inside a code fence, halfway through a table, in the middle of a link
 * destination, between a `**` and its partner, or between the two halves of an emoji. Rendered as is, each of those flashes
 * raw syntax (a wall of grey code that is really prose, pipes where a table will be, a half link) and then jumps when the
 * rest arrives. `stabilizeMarkdown` returns text for the renderer that looks like what the finished text will look like:
 *
 *  - an unclosed fence (``` or ~~~, any info string) is closed, so streamed code is a code block from its first line;
 *  - a table whose header has no complete delimiter row yet is held back until it can render as a table;
 *  - a link or image destination that has not closed shows its text without the half URL;
 *  - unclosed emphasis, strong, strikethrough and inline code are closed; delimiters that cannot open anything yet are dropped;
 *  - a half entity (`&am`), a half tag (`<di`), a lone surrogate half and a bare list/heading marker are dropped. (`&a` stays:
 *    "Q&A" and "AT&T" are far likelier than the first letter of an entity.)
 *
 * It only ever trims or appends at the end of the last block, so text the reader has already seen never rewrites itself
 * further back. Complete, well-formed text comes back unchanged. HTML stays inert: the renderer's `skipHtml` policy is
 * untouched; the half tag is only dropped so it does not flash as text.
 */

export type StabilizeOptions = {
  /** False returns the text untouched (the answer is complete). Defaults to true: this function exists for streaming. */
  streaming?: boolean;
};

export function stabilizeMarkdown(text: string, { streaming = true }: StabilizeOptions = {}): string {
  if (!streaming || !text) return text;
  const clean = dropSplitSurrogates(text);
  const fences = scanFences(clean);
  return fences.open ? closeFence(clean, fences.open) : stabilizeTail(clean, fences.lastClosedEnd);
}

// ---------------------------------------------------------------------------------------------------------------------
// Unicode

/** A surrogate half whose partner has not arrived (or never will) renders as a replacement glyph: drop it. */
function dropSplitSurrogates(text: string): string {
  if (!/[\uD800-\uDFFF]/.test(text)) return text;
  return text.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]|[\uD800-\uDFFF]/g, (match) => match.length === 2 ? match : "");
}

const isWhitespace = (char: string) => char === "" || /\s/u.test(char);
const isPunctuation = (char: string) => char !== "" && /[\p{P}\p{S}]/u.test(char);

function charBefore(text: string, index: number): string {
  if (index <= 0) return "";
  const code = text.charCodeAt(index - 1);
  return code >= 0xdc00 && code <= 0xdfff && index >= 2 ? text.slice(index - 2, index) : text[index - 1];
}
function charAt(text: string, index: number): string {
  const point = text.codePointAt(index);
  return point === undefined ? "" : String.fromCodePoint(point);
}

// ---------------------------------------------------------------------------------------------------------------------
// Fences

type OpenFence = { prefix: string; char: string; length: number; line: number };
type FenceScan = { open: OpenFence | null; lastClosedEnd: number };

const fenceOpener = /^([ \t>]*)(`{3,}|~{3,})(.*)$/;

/** Walks the lines once: whether a fence is still open at the end, and where the last closed one ended. */
function scanFences(text: string): FenceScan {
  let open: OpenFence | null = null;
  let lastClosedEnd = 0;
  let start = 0;
  let line = 0;
  while (start <= text.length) {
    const newline = text.indexOf("\n", start);
    const end = newline === -1 ? text.length : newline;
    const content = text.slice(start, end);
    if (open) {
      const closer = /^[ \t>]*(`{3,}|~{3,})[ \t]*$/.exec(content);
      if (closer && closer[1][0] === open.char && closer[1].length >= open.length) { open = null; lastClosedEnd = Math.min(end + 1, text.length); }
    } else {
      const opener = fenceOpener.exec(content);
      // An info string after backticks cannot itself contain a backtick (that would be inline code).
      if (opener && !(opener[2][0] === "`" && opener[3].includes("`"))) open = { prefix: opener[1], char: opener[2][0], length: opener[2].length, line };
    }
    if (newline === -1) break;
    start = newline + 1;
    line++;
  }
  return { open, lastClosedEnd };
}

function closeFence(text: string, fence: OpenFence): string {
  let body = text;
  const lastBreak = body.lastIndexOf("\n");
  // The line being written may be the start of the closing fence ("``"): it is not code, so leave it out. (Never the opener itself.)
  if (lastBreak !== -1 && body.split("\n").length - 1 > fence.line) {
    const partial = new RegExp(`^[ \\t>]*(?:\\${fence.char}){1,${fence.length - 1}}[ \\t]*$`);
    if (partial.test(body.slice(lastBreak + 1))) body = body.slice(0, lastBreak + 1);
  }
  return `${body}${body.endsWith("\n") ? "" : "\n"}${fence.prefix}${fence.char.repeat(fence.length)}`;
}

// ---------------------------------------------------------------------------------------------------------------------
// The last block

/**
 * A line that is only the start of something: a list, heading or quote marker, a setext underline, half a fence, or an
 * emphasis delimiter with nothing after it.
 */
const loneMarker = /^ {0,3}(?:[-*+]|\d{1,9}[.)]|#{1,6}|>|=+|-{1,2}|`{1,2}|~{1,2}|\*\*|__)[ \t]*$/;

function stabilizeTail(text: string, afterLastFence: number): string {
  const trimmed = dropPartialTail(text);
  // The block being written: everything after the last blank line (and after any closed fence, whose own blank lines don't count).
  const search = trimmed.slice(afterLastFence);
  const blank = /\n[ \t]*\n/g;
  let blockStart = afterLastFence;
  for (let match = blank.exec(search); match; match = blank.exec(search)) blockStart = afterLastFence + match.index + match[0].length;
  if (blockStart > trimmed.length) return trimmed;
  const head = trimmed.slice(0, blockStart);
  let lines = trimmed.slice(blockStart).split("\n");

  // A trailing newline means the line before it is the one being written.
  const last = lines[lines.length - 1] === "" ? lines.length - 2 : lines.length - 1;
  // A number alone on the first line of a block is most likely an ordered-list marker whose "." has not arrived yet ("2" of
  // "2. Next"): shown, it would flash as a paragraph and vanish a frame later.
  if (last >= 0 && (loneMarker.test(lines[last]) || (last === 0 && /^ {0,3}\d{1,9}[ \t]*$/.test(lines[last])))) lines = lines.slice(0, last);
  lines = holdBackHalfTable(lines);
  const block = lines.join("\n");
  return head + repairInline(block);
}

/** A half tag, half entity or bare `<` at the very end of the text. */
function dropPartialTail(text: string): string {
  return text
    .replace(/<\/?[a-zA-Z][^<>\n`]*$|<!--[^\n`]*$|<![^<>\n`]*$|<\/?$/, "")
    .replace(/&(?:#\d{0,7}|#[xX][0-9a-fA-F]{0,6}|[a-zA-Z]{2,10})$/, "");
}

// Tables ---------------------------------------------------------------------------------------------------------------

function cellsOf(line: string): string[] {
  let body = line.trim();
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|") && !body.endsWith("\\|")) body = body.slice(0, -1);
  const cells: string[] = [];
  let current = "";
  for (let i = 0; i < body.length; i++) {
    if (body[i] === "\\" && body[i + 1] === "|") { current += "\\|"; i++; } else if (body[i] === "|") { cells.push(current); current = ""; } else current += body[i];
  }
  cells.push(current);
  return cells;
}

const completeDelimiter = (line: string, headerCells: number) => {
  const cells = cellsOf(line);
  return cells.length === headerCells && cells.every((cell) => /^\s*:?-+:?\s*$/.test(cell));
};
const partialDelimiter = (line: string) => /^[\s|:-]*\|[\s|:-]*$/.test(line);

/**
 * A table is a header row plus a delimiter row. Until the delimiter is complete the lines would render as plain text with
 * pipes, then jump into a table: hold them back. Once the table exists, a body row that is only a pipe is held back too.
 */
function holdBackHalfTable(lines: string[]): string[] {
  const end = lines.length && lines[lines.length - 1] === "" ? lines.length - 1 : lines.length;
  let start = end;
  while (start > 0 && lines[start - 1].includes("|")) start--;
  const run = lines.slice(start, end);
  if (!run.length) return lines;
  if (run.length === 1) return run[0].trimStart().startsWith("|") ? lines.slice(0, start) : lines;
  if (completeDelimiter(run[1], cellsOf(run[0]).length)) return /^\s*\|\s*$/.test(run[run.length - 1]) ? lines.slice(0, end - 1) : lines;
  if (run.length === 2 && partialDelimiter(run[1])) return lines.slice(0, start);
  return lines;
}

// Inline syntax --------------------------------------------------------------------------------------------------------

type Delimiter = { char: string; length: number };

/** Lines that start a new block never continue the emphasis of the line before. */
const startsBlock = /^\s*(?:[-*+]\s|\d{1,9}[.)]\s|#{1,6}(?:\s|$)|>|\|)/;

type InlineScan = {
  /** The text from `from` on is a half link or image: show `replacement` instead. */
  dangling: { from: number; replacement: string } | null;
  open: Delimiter[];
  /** An inline code span that never closed: where its opening backticks start and how many there are. */
  code: { at: number; length: number } | null;
};

function findClosingRun(text: string, from: number, length: number): number {
  for (let i = text.indexOf("`", from); i !== -1; i = text.indexOf("`", i)) {
    let end = i;
    while (text[end] === "`") end++;
    if (end - i === length) return end;
    i = end;
  }
  return -1;
}

function scanInline(text: string): InlineScan {
  const open: Delimiter[] = [];
  let brackets: Array<{ at: number; image: boolean }> = [];
  for (let i = 0; i < text.length;) {
    const char = text[i];
    if (char === "\\") { i += 2; continue; }
    if (char === "`") {
      let end = i;
      while (text[end] === "`") end++;
      const close = findClosingRun(text, end, end - i);
      if (close === -1) return { dangling: null, open, code: { at: i, length: end - i } };
      i = close;
      continue;
    }
    if (char === "\n") {
      if (startsBlock.test(text.slice(i + 1, text.indexOf("\n", i + 1) === -1 ? undefined : text.indexOf("\n", i + 1)))) { open.length = 0; brackets = []; }
      i++;
      continue;
    }
    if (char === "|") { open.length = 0; i++; continue; } // emphasis never spans table cells
    if (char === "[") { brackets.push({ at: i, image: text[i - 1] === "!" }); i++; continue; }
    if (char === "]") {
      const bracket = brackets.pop();
      // An image whose alt text has just closed and nothing follows: its destination has not started, so it is not an image yet.
      if (bracket?.image && i + 1 === text.length) return { dangling: { from: bracket.at - 1, replacement: "" }, open, code: null };
      if (bracket && text[i + 1] === "(") {
        let depth = 1;
        let j = i + 2;
        for (; j < text.length && depth > 0; j++) {
          const c = text[j];
          if (c === "\\") j++;
          else if (c === "\n") break;
          else if (c === "(") depth++;
          else if (c === ")") depth--;
        }
        if (depth > 0 && j >= text.length) {
          return { dangling: { from: bracket.image ? bracket.at - 1 : bracket.at, replacement: bracket.image ? "" : text.slice(bracket.at + 1, i) }, open, code: null };
        }
        i = j;
        continue;
      }
      i++;
      continue;
    }
    if (char === "*" || char === "_" || char === "~") {
      let end = i;
      while (text[end] === char) end++;
      const length = end - i;
      if (char === "~" && length > 2) { i = end; continue; }
      const before = charBefore(text, i);
      const after = charAt(text, end);
      const left = !isWhitespace(after) && (!isPunctuation(after) || isWhitespace(before) || isPunctuation(before));
      const right = !isWhitespace(before) && (!isPunctuation(before) || isWhitespace(after) || isPunctuation(after));
      // `_` never opens inside a word. `*` can (CJK has no spaces), except between two ASCII word characters, where it is
      // arithmetic or a glob far more often than emphasis ("3*4=12").
      const asciiWord = /[A-Za-z0-9]/;
      const canOpen = char === "_" ? left && (!right || isPunctuation(before))
        : char === "*" ? left && !(asciiWord.test(before) && asciiWord.test(after))
        : left && length === 2; // strikethrough opens with exactly two tildes; one tilde may still be half of a closer
      const canClose = char === "_" ? right && (!left || isPunctuation(after)) : right;
      let remaining = length;
      if (canClose) {
        while (remaining > 0) {
          const index = open.findLastIndex((delimiter) => delimiter.char === char);
          if (index === -1) break;
          const used = Math.min(remaining, open[index].length);
          remaining -= used;
          open.length = index + 1; // anything opened inside the span that just closed never closes
          open[index] = { char, length: open[index].length - used };
          if (open[index].length === 0) open.pop();
        }
      }
      if (remaining > 0 && canOpen) open.push({ char, length: remaining });
      i = end;
      continue;
    }
    i++;
  }
  // An image whose alt text is still arriving. (A link's bracket is left as typed: it may just be a bracket.)
  const unclosed = brackets[brackets.length - 1];
  if (unclosed?.image) return { dangling: { from: unclosed.at - 1, replacement: "" }, open, code: null };
  return { dangling: null, open, code: null };
}

function repairInline(block: string): string {
  // A run of emphasis characters after a space at the very end has nothing to open yet.
  let text = block.replace(/([ \t])(?:\*{1,3}|_{1,3}|~~)$/, "$1");
  let scan = scanInline(text);
  if (scan.dangling) {
    text = text.slice(0, scan.dangling.from) + scan.dangling.replacement;
    scan = scanInline(text);
  }
  if (scan.code) {
    // A code span with nothing in it yet has nothing to show; otherwise close it. Emphasis opened before it closes after it.
    const { at, length } = scan.code;
    text = text.slice(at + length).trim() ? text + "`".repeat(length) : text.slice(0, at);
  }
  if (!scan.open.length) return text;
  const closers = scan.open.map((delimiter) => delimiter.char.repeat(delimiter.length)).reverse().join("");
  return text.replace(/[ \t\n]+$/, "") + closers;
}
