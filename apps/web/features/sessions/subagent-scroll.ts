type Position = { scrollTop: number; scrollHeight: number; clientHeight: number };
const distance = (position: Position) => Math.max(0, position.scrollHeight - position.clientHeight - position.scrollTop);

/** Follow the child independently of the conversation, until the reader leaves its live edge. */
export function createSubagentScroll(initiallyFollowing: boolean) {
  let following = initiallyFollowing;
  return {
    pause() { following = false; },
    resume() { following = true; },
    scrolled(position: Position) { following = distance(position) <= 24; },
    layout(position: Position) { return following ? Math.max(0, position.scrollHeight - position.clientHeight) : null; },
    jumpVisible(position: Position) { return !following && distance(position) > 24; },
  };
}
