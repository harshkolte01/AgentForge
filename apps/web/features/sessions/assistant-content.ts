/** Only leading provider thinking tags are metadata; examples in the answer stay untouched. */
export function splitAssistantContent(content: string, streaming = false) {
  let answer = content;
  const thoughts: string[] = [];
  while (answer.trimStart()) {
    const start = answer.trimStart();
    const opening = /^<(think|thinking)>/.exec(start);
    if (!opening) {
      if (streaming && ["<think>", "<thinking>"].some((tag) => tag.startsWith(start))) {
        return { answer: "", reasoning: thoughts.join("\n\n"), isThinking: true };
      }
      break;
    }
    const closing = `</${opening[1]}>`;
    const body = start.slice(opening[0].length);
    const end = body.indexOf(closing);
    if (end === -1) {
      // A closing tag can arrive over several chunks. Keep its partial syntax out of the UI.
      let text = body;
      if (streaming) {
        for (let length = closing.length - 1; length > 0; length--) {
          if (text.endsWith(closing.slice(0, length))) { text = text.slice(0, -length); break; }
        }
      }
      thoughts.push(text.trim());
      return { answer: "", reasoning: thoughts.join("\n\n"), isThinking: streaming };
    }
    thoughts.push(body.slice(0, end).trim());
    answer = body.slice(end + closing.length).trimStart();
  }
  return { answer, reasoning: thoughts.length ? thoughts.join("\n\n") : null, isThinking: false };
}
