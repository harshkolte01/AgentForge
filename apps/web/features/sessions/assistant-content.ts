/**
 * Separates the answer from legacy provider metadata. Some providers once put their reasoning in a leading
 * `<think>`/`<thinking>` block; that is never shown in chat. Only a block at the very start counts: the same tags inside an
 * answer (an example, a quoted prompt) are the answer's own words and stay untouched.
 */
export function splitAssistantContent(content: string, streaming = false): { answer: string; hadLeadingThoughts: boolean } {
  let answer = content;
  let hadLeadingThoughts = false;
  while (answer.trimStart()) {
    const start = answer.trimStart();
    const opening = /^<(think|thinking)>/.exec(start);
    if (!opening) {
      // The opening tag itself may be split across chunks: show nothing until it is clear whether this is metadata.
      if (streaming && ["<think>", "<thinking>"].some((tag) => tag.startsWith(start))) return { answer: "", hadLeadingThoughts };
      break;
    }
    hadLeadingThoughts = true;
    const closing = `</${opening[1]}>`;
    const body = start.slice(opening[0].length);
    const end = body.indexOf(closing);
    // No closing tag (yet): everything after the opening tag is still metadata, and a partial closing tag is not an answer.
    if (end === -1) return { answer: "", hadLeadingThoughts };
    answer = body.slice(end + closing.length).trimStart();
  }
  return { answer, hadLeadingThoughts };
}
