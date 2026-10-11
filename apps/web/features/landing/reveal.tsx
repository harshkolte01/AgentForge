import type { ReactNode } from "react";

/** Public content stays visible, including before hydration and when scripts fail. */
export function Reveal({ children, className = "" }: { children: ReactNode; delay?: number; className?: string }) {
  return <div className={className}>{children}</div>;
}
