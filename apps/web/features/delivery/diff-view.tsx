"use client";
import { memo, useMemo, useState } from "react";
import type { ChangedFile } from "@/lib/delivery-api";
import { parsePatch, plural, type DiffRow } from "@/lib/delivery-status";

/** A very long diff is shown in pages so the panel stays fast. */
const PAGE_ROWS = 600;
const noteClass = "px-3 py-3 text-xs leading-5 text-muted-foreground";

const rowClass: Record<"add" | "del" | "ctx", { row: string; gutter: string; sign: string; label: string }> = {
  add: { row: "bg-emerald-500/10 dark:bg-emerald-400/10", gutter: "bg-emerald-500/15 dark:bg-emerald-400/15", sign: "+", label: "Added: " },
  del: { row: "bg-red-500/10 dark:bg-red-400/10", gutter: "bg-red-500/15 dark:bg-red-400/15", sign: "-", label: "Removed: " },
  ctx: { row: "", gutter: "", sign: " ", label: "" },
};

function Row({ row }: { row: DiffRow }) {
  if (row.kind === "hunk") return <div className="flex min-w-full bg-muted/60 px-3 py-0.5 text-muted-foreground"><span className="whitespace-pre">{row.text}</span></div>;
  if (row.kind === "note") return <div className="flex min-w-full px-3 py-0.5 italic text-muted-foreground"><span className="whitespace-pre">{row.text}</span></div>;
  const style = rowClass[row.kind];
  return <div className={`flex min-w-full ${style.row}`}>
    <span aria-hidden="true" className={`flex shrink-0 select-none text-right text-muted-foreground/70 ${style.gutter}`}>
      <span className="w-9 pr-1.5">{row.oldLine ?? ""}</span><span className="w-9 pr-1.5">{row.newLine ?? ""}</span>
    </span>
    <span aria-hidden="true" className="w-4 shrink-0 select-none text-center text-muted-foreground">{style.sign}</span>
    <span className="whitespace-pre pr-3">{style.label ? <span className="sr-only">{style.label}</span> : null}{row.text || " "}</span>
  </div>;
}

/** One file's unified diff: line numbers, added and removed lines marked by colour, sign and text for screen readers. */
export const DiffView = memo(function DiffView({ file }: { file: ChangedFile }) {
  const rows = useMemo(() => (file.binary ? [] : parsePatch(file.patch)), [file.binary, file.patch]);
  const [shown, setShown] = useState(PAGE_ROWS);
  if (file.binary) return <p className={noteClass}>Binary file: there is no preview.</p>;
  if (!rows.length) return <p className={noteClass}>{file.status === "deleted" ? "The file is deleted." : file.truncated ? "This file's diff is too large to show." : "No line changes to show (its mode or name may have changed)."}</p>;
  const visible = rows.length > shown ? rows.slice(0, shown) : rows;
  return <div>
    <div role="region" tabIndex={0} aria-label={`Changes in ${file.path}`} className="overflow-x-auto overscroll-x-contain py-1 font-mono text-[12px] leading-5 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60">
      <div className="w-max min-w-full">{visible.map((row, index) => <Row key={index} row={row} />)}</div>
    </div>
    {rows.length > shown ? <div className="border-t px-3 py-1.5"><button type="button" className="inline-flex min-h-6 items-center rounded px-1 text-xs font-medium underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 pointer-coarse:min-h-11" onClick={() => setShown((count) => count + PAGE_ROWS)}>Show {plural(Math.min(PAGE_ROWS, rows.length - shown), "more line")}</button></div> : null}
    {file.truncated ? <p className={`${noteClass} border-t`}>This diff was cut short because the file is large. Only the beginning is shown.</p> : null}
  </div>;
});
