// A fake clock for the recovery controller: time only moves when a test advances it, and promises are drained between
// timers so async work started by a timer finishes before the next one fires.
export const flush = async (rounds = 3) => { for (let i = 0; i < rounds; i++) await new Promise((resolve) => setImmediate(resolve)); };

export function fakeClock(start = 1_000_000) {
  let now = start;
  let ids = 0;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout(callback, ms) { const id = ++ids; timers.set(id, { id, at: now + Math.max(0, ms), callback }); return id; },
    clearTimeout(id) { timers.delete(id); },
    /** Timers that have not fired yet, soonest first: [{ in, id }]. */
    pending: () => [...timers.values()].sort((a, b) => a.at - b.at || a.id - b.id).map((timer) => ({ id: timer.id, in: timer.at - now })),
    get size() { return timers.size; },
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        await flush();
        const due = [...timers.values()].filter((timer) => timer.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
        if (!due) break;
        timers.delete(due.id);
        now = Math.max(now, due.at);
        due.callback();
      }
      now = end;
      await flush();
    },
  };
}
