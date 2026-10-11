"use client";
import { useState, type ReactNode } from "react";
import { ChevronDown, RefreshCw, Search } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { focusClass } from "@/features/settings/settings-ui";

/**
 * The quiet list language shared by the Plugins and Skills panels: a toolbar (tabs on the left; search, refresh and an Add menu on
 * the right), sections with a small heading, and borderless rows: a tile, a name, one line of text and one control.
 */

const pillClass = "h-10 rounded-full";

export function Toolbar({ tabs, children }: { tabs?: ReactNode; children: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
    {tabs ? <div className="min-w-0">{tabs}</div> : null}
    <div className={`flex w-full items-center gap-2 sm:w-auto ${tabs ? "" : "sm:ml-auto"}`}>{children}</div>
  </div>;
}

export function SearchField({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return <label className="relative min-w-0 flex-1 sm:w-60 sm:flex-none">
    <span className="sr-only">{label}</span>
    <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
    <input type="search" value={value} onChange={(event) => onChange(event.target.value)} placeholder={label} autoComplete="off" autoCapitalize="none" spellCheck={false}
      className={`${pillClass} w-full border border-input bg-transparent pl-10 pr-4 text-sm outline-none transition-colors placeholder:text-muted-foreground hover:bg-muted/40 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none [&::-webkit-search-cancel-button]:hidden`} />
  </label>;
}

export function RefreshButton({ label, busy, onClick }: { label: string; busy: boolean; onClick: () => void }) {
  return <button type="button" aria-label={label} title={label} disabled={busy} onClick={onClick}
    className={`grid size-10 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-60 motion-reduce:transition-none ${focusClass}`}>
    <RefreshCw className={`size-4 ${busy ? "motion-safe:animate-spin" : ""}`} aria-hidden="true" /></button>;
}

/**
 * "Add" with a short list of choices under it. `children` gets `close`, to call when an item is picked. Plain buttons in a popover,
 * not an ARIA menu: Tab moves between them, and a menu role would promise arrow-key handling this does not have.
 */
export function AddMenu({ label = "Add", disabled = false, children }: { label?: string; disabled?: boolean; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger disabled={disabled} className={`${pillClass} inline-flex shrink-0 items-center gap-1.5 bg-primary pl-5 pr-3.5 text-sm font-medium text-primary-foreground outline-none transition-colors hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none`}>
      {label}<ChevronDown className={`size-4 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`} aria-hidden="true" /></PopoverTrigger>
    <PopoverContent side="bottom" align="end" aria-label={label} className="w-[min(18rem,calc(100vw-1.5rem))] p-1.5">
      {children(() => setOpen(false))}
    </PopoverContent>
  </Popover>;
}

export function MenuItem({ icon, children, hint, disabled, onClick }: { icon?: ReactNode; children: ReactNode; hint?: ReactNode; disabled?: boolean; onClick: () => void }) {
  return <button type="button" disabled={disabled} onClick={onClick}
    className="flex min-h-11 w-full items-center gap-3 rounded-xl px-2.5 py-1.5 text-left text-sm outline-none transition-colors hover:bg-muted focus-visible:bg-muted disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none sm:min-h-10">
    {icon}<span className="min-w-0 flex-1"><span className="block truncate">{children}</span>{hint ? <span className="block truncate text-xs text-muted-foreground">{hint}</span> : null}</span>
  </button>;
}

/** A heading over a list, with a small count. */
export function ListSection({ title, count, note, children }: { title: string; count?: ReactNode; note?: ReactNode; children: ReactNode }) {
  return <section className="space-y-3" aria-label={title}>
    <div className="space-y-1"><h3 className="flex items-baseline gap-2 text-base font-semibold tracking-tight">{title}{count !== undefined ? <span className="text-sm font-normal tabular-nums text-muted-foreground">{count}</span> : null}</h3>
      {note ? <p className="max-w-prose text-[0.8125rem] leading-5 text-muted-foreground">{note}</p> : null}</div>
    {children}
  </section>;
}

/** Rows in two columns on a wide screen, one on a narrow one. */
export function RowList({ label, children }: { label: string; children: ReactNode }) {
  return <ul aria-label={label} className="grid grid-cols-1 gap-x-10 gap-y-0.5 md:grid-cols-2">{children}</ul>;
}

/**
 * One borderless row. The left side (tile, name, line of text) opens the thing when `onOpen` is given; `trailing` is its own
 * control (a plus, a switch), so there is never a button inside a button.
 */
export function ListRow({ leading, title, subtitle, trailing, onOpen, openLabel, rowProps }: {
  leading: ReactNode; title: ReactNode; subtitle: ReactNode; trailing?: ReactNode; onOpen?: () => void; openLabel?: string; rowProps?: Record<string, string>;
}) {
  const body = <>{leading}<span className="min-w-0 flex-1"><span className="block truncate text-[15px] font-medium leading-5">{title}</span><span className="mt-1 block truncate text-sm leading-5 text-muted-foreground">{subtitle}</span></span></>;
  return <li className="group -mx-3 flex items-center gap-1 rounded-2xl transition-colors hover:bg-muted/50 focus-within:bg-muted/50 motion-reduce:transition-none" {...rowProps}>
    {onOpen
      ? <button type="button" onClick={onOpen} aria-label={openLabel} className={`flex min-w-0 flex-1 items-center gap-4 rounded-2xl px-3 py-3 text-left ${focusClass}`}>{body}</button>
      : <div className="flex min-w-0 flex-1 items-center gap-4 px-3 py-3">{body}</div>}
    {trailing ? <div className="flex shrink-0 items-center pr-3">{trailing}</div> : null}
  </li>;
}

/** The round "+" at the end of a row. */
export function PlusButton({ label, disabled, onClick, icon }: { label: string; disabled?: boolean; onClick: () => void; icon: ReactNode }) {
  return <button type="button" aria-label={label} title={label} disabled={disabled} onClick={onClick}
    className={`grid size-9 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-background hover:text-foreground disabled:pointer-events-none disabled:opacity-40 motion-reduce:transition-none ${focusClass}`}>{icon}</button>;
}

export function RowSkeleton({ label, rows = 4 }: { label: string; rows?: number }) {
  return <div role="status" className="grid grid-cols-1 gap-x-10 md:grid-cols-2"><span className="sr-only">{label}</span>
    {Array.from({ length: rows }, (_, index) => <div key={index} className="-mx-3 flex items-center gap-4 px-3 py-3" aria-hidden="true">
      <div className="size-11 shrink-0 animate-pulse rounded-xl bg-muted motion-reduce:animate-none" />
      <div className="flex-1 space-y-2"><div className="h-4 w-32 animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-3.5 w-52 max-w-full animate-pulse rounded bg-muted motion-reduce:animate-none" /></div>
    </div>)}
  </div>;
}

export function EmptyLine({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return <p className="flex items-center gap-3 py-2 text-sm leading-6 text-muted-foreground">{icon}<span>{children}</span></p>;
}
