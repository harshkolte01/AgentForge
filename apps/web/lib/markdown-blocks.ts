/**
 * Splits Markdown into top-level blocks that each parse on their own exactly as they would inside the whole document, so a
 * streamed answer can render one memoized parse per block and re-parse only the block that is still growing.
 *
 * A split happens only at a blank line outside a code fence, and only when what follows clearly starts a new top-level block:
 * its first line is not indented (no list continuation or indented code) and it does not add another item to a list the
 * current block holds (that would change the list's numbering or looseness). Tables and blockquotes end at a blank line in
 * Markdown anyway, so they always stay whole. Reference-style link and footnote definitions apply to the whole document, so
 * text that has any is never split. When in doubt the text stays together: that costs only a larger re-parse.
 */

const linkDefinition = /^\s{0,3}\[[^\]]+\]:\s/m;
// A fence opens or closes only when indented 0-3 spaces (CommonMark); a deeper "```" is text inside a code block or a
// list item, and treating it as a fence would split a real code block in two. Fences nested in list items need no
// tracking: a split never happens before an indented line, and an unindented line ends the list item anyway.
const fenceLine = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)\r?$/;
const listItem = /^\s*(?:[-*+]|\d{1,9}[.)])(?:\s|$)/;
const blank = (line: string) => /^\s*$/.test(line);

export function splitMarkdownBlocks(text: string): string[] {
  if (!text) return [];
  if (linkDefinition.test(text)) return [text];
  const lines = text.split("\n");
  const blocks: string[] = [];
  let current: string[] = [];
  let hasList = false;
  let fence: { char: string; length: number } | null = null;
  let pendingBlank = false;

  const flush = () => {
    while (current.length && blank(current[current.length - 1])) current.pop();
    if (current.length) blocks.push(current.join("\n").replace(/\r$/, ""));
    current = [];
    hasList = false;
  };

  for (const line of lines) {
    if (fence) {
      current.push(line);
      const close = fenceLine.exec(line);
      if (close && close[1][0] === fence.char && close[1].length >= fence.length && !close[2].trim()) fence = null;
      continue;
    }
    if (blank(line)) {
      if (current.length) { current.push(line); pendingBlank = true; }
      continue;
    }
    if (pendingBlank) {
      pendingBlank = false;
      const indented = /^\s/.test(line);
      const continuesList = hasList && listItem.test(line);
      if (!indented && !continuesList) flush();
    }
    current.push(line);
    if (listItem.test(line)) hasList = true;
    const open = fenceLine.exec(line);
    // A backtick fence's info string cannot hold a backtick; such a line is inline code, not a fence.
    if (open && !(open[1][0] === "`" && open[2].includes("`"))) fence = { char: open[1][0], length: open[1].length };
  }
  flush();
  return blocks;
}
