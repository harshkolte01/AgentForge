/** A small, original bitmap alphabet for the public AgentForge identity. */
const glyphs: Record<string, string[]> = {
  A: ["01110","11011","11011","11111","11011","11011","11011"],
  G: ["01111","11000","11000","11011","11011","11011","01110"],
  E: ["11111","11000","11000","11110","11000","11000","11111"],
  N: ["11001","11101","11101","11011","11011","11001","11001"],
  T: ["11111","00100","00100","00100","00100","00100","00100"],
  F: ["11111","11000","11000","11110","11000","11000","11000"],
  O: ["01110","11011","11011","11011","11011","11011","01110"],
  R: ["11110","11011","11011","11110","11100","11010","11011"],
};
export function Wordmark({ compact = false, className = "" }: { compact?: boolean; className?: string }) {
  const letters = compact ? "A" : "AGENTFORGE";
  return <svg viewBox={`0 0 ${letters.length * 30 - 5} 35`} fill="currentColor" aria-hidden="true" className={className}>
    {[...letters].flatMap((letter, index) => glyphs[letter].flatMap((row, y) => [...row].map((cell, x) => cell === "1" ? <rect key={`${index}-${y}-${x}`} x={index * 30 + x * 5} y={y * 5} width="4" height="3.6" /> : null)))}
  </svg>;
}
