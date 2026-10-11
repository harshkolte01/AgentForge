"use client";
import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { Check, Plug, TriangleAlert } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { chipClass, optionClass } from "@/features/sessions/composer-styles";
import { pluginsStore, usePlugins } from "@/features/plugins/use-plugins";
import { effectiveSelection, toggled, toolSummary } from "@/lib/plugins-api";
import { routes } from "@/lib/routes";
import { message } from "@/lib/task-api";

const linkClass = "rounded underline underline-offset-4 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60";

/**
 * Which of the person's connected plugins this conversation may use. The plugins are connected in Settings; here each is switched on
 * or off for the conversation, and the agent is offered the tools of the ones that are on. `selected` is the conversation's own
 * list, or null for a conversation not created yet (it starts with the plugins left on in Settings).
 */
export function PluginPicker({ selected, disabled, onChange, side = "top" }: { selected: readonly string[] | null; disabled: boolean; onChange: (ids: string[]) => Promise<void>; side?: "top" | "bottom" }) {
  const descriptionId = useId();
  const { status, overview, error } = usePlugins();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const connections = overview?.connections ?? [];
  const chosen = effectiveSelection(connections, selected);
  // Opening the menu reads the list again, so a plugin connected in another tab is there; the old list stays on screen meanwhile.
  useEffect(() => { if (open && status !== "loading") pluginsStore.reload(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  if (overview && !overview.enabled) return null;
  async function toggle(id: string, on: boolean) {
    setBusy(true); setFailure("");
    try { await onChange(toggled(connections, chosen, id, on)); }
    catch (problem) { setFailure(message(problem)); }
    finally { setBusy(false); }
  }
  const count = chosen.length;
  const label = count ? `Plugins: ${count} on` : "Plugins";
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger disabled={disabled} aria-label={label} title={label} className={`${chipClass} justify-center max-sm:w-11 max-sm:px-0`}>
      <Plug className="size-4" aria-hidden="true" />
      <span className="hidden sm:inline">Plugins</span>
      {count ? <span aria-hidden="true" className="min-w-4 rounded-full bg-foreground px-1 text-center text-[10px] font-medium leading-4 text-background">{count}</span> : null}
    </PopoverTrigger>
    <PopoverContent side={side} align="start" className="w-[min(19rem,calc(100vw-1.5rem))] rounded-xl shadow-sm">
      <div className="p-1" role="group" aria-label="Plugins for this conversation" aria-describedby={descriptionId}>
        <p id={descriptionId} className="px-2.5 pb-1 pt-2 text-xs leading-5 text-muted-foreground">The agent can use the tools of the plugins that are on.</p>
        {!overview && status !== "error" ? <div role="status" className="space-y-2 px-2.5 py-2"><span className="sr-only">Loading plugins…</span><div className="h-4 w-40 animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-4 w-28 animate-pulse rounded bg-muted motion-reduce:animate-none" /></div> : null}
        {status === "error" && !overview ? <p role="alert" className="px-2.5 py-2 text-xs leading-5 text-destructive">{error} <button type="button" className={linkClass} onClick={() => pluginsStore.reload()}>Try again</button></p> : null}
        {overview && !connections.length ? <p className="px-2.5 py-2 text-[13px] leading-5">No plugins are connected yet.</p> : null}
        {connections.map((connection) => {
          const on = chosen.includes(connection.id);
          return <button key={connection.id} type="button" role="checkbox" aria-checked={on} aria-label={`${connection.name}: ${connection.status === "error" ? "needs attention" : toolSummary(connection)}`} disabled={busy} className={`${optionClass} disabled:opacity-60`} onClick={() => void toggle(connection.id, !on)}>
            <span className="min-w-0 flex-1">
              <span className="block truncate">{connection.name}</span>
              <span className="block truncate text-xs text-muted-foreground">{connection.status === "error" ? <span className="inline-flex items-center gap-1"><TriangleAlert className="size-3" aria-hidden="true" />Needs attention in Settings</span> : toolSummary(connection)}</span>
            </span>
            <Check className={`size-3.5 shrink-0 ${on ? "" : "invisible"}`} aria-hidden="true" />
          </button>;
        })}
        {failure ? <p role="alert" className="px-2.5 py-1 text-xs leading-5 text-destructive">{failure}</p> : null}
        <div className="mt-1 border-t px-2.5 py-2 text-xs leading-5 text-muted-foreground"><Link href={routes.settings("plugins")} className={linkClass}>{connections.length ? "Manage plugins" : "Add a plugin"}</Link></div>
      </div>
    </PopoverContent>
  </Popover>;
}
