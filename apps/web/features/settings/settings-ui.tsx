import type { ReactNode } from "react";

/**
 * The building blocks every Settings section is made from, so they share one width, one rhythm and one look:
 * a header, then groups (a small heading above a bordered card of rows). Moving between sections never shifts the page.
 */
export const sectionClass = "max-w-3xl space-y-8";
export const focusClass = "outline-none focus-visible:ring-2 focus-visible:ring-ring/60";
export const subsectionClass = "space-y-4 border-t pt-5";
export const subheadingClass = "text-sm font-medium";
/** One bordered surface holding rows, a list or a form. Rows inside it are separated by hairlines. */
export const cardClass = "overflow-hidden rounded-xl border bg-card text-card-foreground shadow-xs";
export const rowClass = "flex flex-wrap items-center justify-between gap-x-6 gap-y-3 px-4 py-3.5 sm:px-5";
/** One choice in a radio group: a native radio (arrow keys move between choices) with its description beside it. */
export const choiceClass = "group flex cursor-pointer gap-3.5 rounded-xl border bg-card p-4 transition-colors hover:bg-muted/40 has-[:checked]:border-foreground/40 has-[:checked]:bg-muted/40 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/60 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60 motion-reduce:transition-none";
export const radioClass = "size-4 shrink-0 accent-primary outline-none";
/** A small rounded tile that carries an icon, so rows and cards line up on one visual rhythm. */
export const iconTileClass = "grid size-9 shrink-0 place-items-center rounded-lg border bg-muted/50 text-muted-foreground [&>svg]:size-4";
export const chipClass = "inline-flex max-w-full items-center rounded-md border bg-muted/40 px-2 py-0.5 text-xs text-foreground/80";

export function SectionHeader({ id, title, description, action }: { id: string; title: ReactNode; description: ReactNode; action?: ReactNode }) {
  return <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
    <div className="min-w-0 flex-1 basis-64 space-y-1.5"><h2 id={id} className="text-xl font-semibold tracking-tight">{title}</h2><p className="max-w-prose text-sm leading-6 text-muted-foreground">{description}</p></div>
    {action}
  </header>;
}

/** A small heading (and optional note) above the card or controls it names. */
export function Group({ title, note, aside, children }: { title: ReactNode; note?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return <div className="space-y-3">
    <div className="flex items-end justify-between gap-3"><div className="min-w-0 space-y-1"><h3 className="text-sm font-medium">{title}</h3>{note ? <p className="max-w-prose text-[0.8125rem] leading-5 text-muted-foreground">{note}</p> : null}</div>{aside}</div>
    {children}
  </div>;
}

/** Label and value pairs as a real description list, one pair per row of a card. */
export function Facts({ children }: { children: ReactNode }) {
  return <dl className={`${cardClass} divide-y`}>{children}</dl>;
}
export function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-x-6 gap-y-1 px-4 py-3.5 text-sm sm:grid-cols-[10rem_minmax(0,1fr)] sm:items-center sm:px-5"><dt className="text-muted-foreground">{label}</dt><dd className="min-w-0 break-words">{children}</dd></div>;
}

/** Placeholder facts the same height as the loaded ones (20px lines), so nothing moves when the data arrives. */
export function SkeletonRows({ count, label }: { count: number; label: string }) {
  return <div role="status" className={`${cardClass} divide-y`}>
    <span className="sr-only">{label}</span>
    {Array.from({ length: count }, (_, index) => <div key={index} className="grid grid-cols-1 gap-x-6 gap-y-1 px-4 py-3.5 sm:grid-cols-[10rem_minmax(0,1fr)] sm:items-center sm:px-5" aria-hidden="true">
      <div className="h-5 w-24 animate-pulse rounded bg-muted motion-reduce:animate-none" />
      <div className={`h-5 animate-pulse rounded bg-muted motion-reduce:animate-none ${index % 2 ? "w-28" : "w-40"}`} />
    </div>)}
  </div>;
}

/** A calm "nothing here yet" panel: an icon, a line of what it is, and optionally the one thing to do about it. */
export function EmptyState({ icon, title, children, action }: { icon: ReactNode; title: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-12 text-center">
    <span className={iconTileClass} aria-hidden="true">{icon}</span>
    <div className="space-y-1"><p className="text-sm font-medium">{title}</p>{children ? <p className="mx-auto max-w-sm text-sm leading-6 text-muted-foreground">{children}</p> : null}</div>
    {action}
  </div>;
}

/** An on/off setting. A native checkbox (so it is a form control to every assistive tool) drawn as a switch. */
export function Switch({ checked, onChange, describedBy, label }: { checked: boolean; onChange: (checked: boolean) => void; describedBy?: string; label: string }) {
  return <span className="relative inline-flex shrink-0">
    <input type="checkbox" role="switch" checked={checked} onChange={(event) => onChange(event.target.checked)} aria-label={label} aria-describedby={describedBy} className="peer absolute inset-0 z-10 size-full cursor-pointer opacity-0" />
    <span aria-hidden="true" className="h-6 w-10 rounded-full border bg-muted transition-colors before:absolute before:left-0.5 before:top-0.5 before:size-5 before:rounded-full before:bg-muted-foreground/70 before:shadow-sm before:transition-all peer-checked:border-transparent peer-checked:bg-primary peer-checked:before:translate-x-4 peer-checked:before:bg-primary-foreground peer-focus-visible:ring-2 peer-focus-visible:ring-ring/60 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background motion-reduce:transition-none motion-reduce:before:transition-none" />
  </span>;
}

export function StatusDot({ tone }: { tone: "ok" | "warn" | "off" }) {
  return <span aria-hidden="true" className={`inline-block size-2 shrink-0 rounded-full ${tone === "ok" ? "bg-emerald-500" : tone === "warn" ? "bg-amber-500" : "bg-muted-foreground/40"}`} />;
}
