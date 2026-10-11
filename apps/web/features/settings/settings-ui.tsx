import type { ReactNode } from "react";

/** Every settings section shares one width, one header shape and one label column, so moving between them never shifts the page. */
export const sectionClass = "max-w-2xl space-y-7";
export const rowsClass = "grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-5 gap-y-4 text-sm";
export const subsectionClass = "space-y-4 border-t pt-5";
export const subheadingClass = "text-sm font-medium";
export const focusClass = "outline-none focus-visible:ring-2 focus-visible:ring-ring/60";
/** One choice in a radio group: a native radio (arrow keys move between choices) with its description beneath. */
export const choiceClass = "flex cursor-pointer gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50 has-[:checked]:border-foreground/30 has-[:checked]:bg-muted/50 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/60 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60 motion-reduce:transition-none";
export const radioClass = "size-4 shrink-0 accent-primary outline-none";

export function SectionHeader({ id, title, description, action }: { id: string; title: ReactNode; description: ReactNode; action?: ReactNode }) {
  return <header className="flex items-start justify-between gap-4">
    <div className="space-y-2"><h2 id={id} className="text-lg font-semibold">{title}</h2><p className="max-w-prose text-sm leading-6 text-muted-foreground">{description}</p></div>
    {action}
  </header>;
}

/** Placeholder rows the same height as the loaded rows (20px lines), so nothing moves when the data arrives. */
export function SkeletonRows({ count, label }: { count: number; label: string }) {
  return <div role="status" className={rowsClass}>
    <span className="sr-only">{label}</span>
    {Array.from({ length: count }, (_, index) => <div key={index} className="contents" aria-hidden="true">
      <div className="h-5 w-24 animate-pulse rounded bg-muted motion-reduce:animate-none" />
      <div className={`h-5 animate-pulse rounded bg-muted motion-reduce:animate-none ${index % 2 ? "w-28" : "w-40"}`} />
    </div>)}
  </div>;
}

export function StatusDot({ tone }: { tone: "ok" | "warn" | "off" }) {
  return <span aria-hidden="true" className={`inline-block size-2 shrink-0 rounded-full ${tone === "ok" ? "bg-emerald-500" : tone === "warn" ? "bg-amber-500" : "bg-muted-foreground/40"}`} />;
}
