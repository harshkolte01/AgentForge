"use client";
import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { ChevronRight, Circle, X } from "lucide-react";
import { checklistMode, setupItems, type GitHubSetup } from "@/features/sessions/first-run-model";
import type { GitHubStatus } from "@/lib/github-api";
import { api } from "@/lib/task-api";
import type { WorkspaceConfiguration } from "@/lib/workspace-api";

const DISMISS_KEY = "agentforge.setup-dismissed";
const dismissListeners = new Set<() => void>();
function readDismissed() { try { return localStorage.getItem(DISMISS_KEY) === "1"; } catch { return false; } }
function dismiss() {
  try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* Hidden for this page only. */ }
  dismissListeners.forEach((listener) => listener());
}
const subscribeDismissed = (listener: () => void) => { dismissListeners.add(listener); return () => { dismissListeners.delete(listener); }; };

const linkClass = "rounded font-medium text-foreground underline decoration-foreground/30 underline-offset-4 outline-none hover:decoration-foreground focus-visible:ring-2 focus-visible:ring-ring/60";

/**
 * Under the empty chat's composer: what is left to set up. It renders nothing until the configuration has loaded and sits
 * outside the centred block, so arriving never moves the composer.
 */
export function FirstRun({ config }: { config: WorkspaceConfiguration | null }) {
  const dismissed = useSyncExternalStore(subscribeDismissed, readDismissed, () => true);
  const [open, setOpen] = useState(false);
  // Undefined until asked. GitHub is optional, so it is only asked about while the list could show at all.
  const [github, setGitHub] = useState<GitHubSetup | undefined>(undefined);
  const asking = !!config && !dismissed;
  useEffect(() => {
    if (!asking) return;
    const controller = new AbortController();
    // The stored answer only (no GitHub request), so the list appears at once on every new conversation.
    void api<GitHubStatus>("/github/status?live=false", { signal: controller.signal }).then(
      (value) => { if (!controller.signal.aborted) setGitHub({ configured: value.configured, connected: value.connected }); },
      () => { if (!controller.signal.aborted) setGitHub(null); });
    return () => controller.abort();
  }, [asking]);
  // Everything appears at once, so nothing below the composer is pushed around as answers arrive.
  if (!config || (asking && github === undefined)) return null;
  const items = setupItems(config, github ?? null);
  const mode = checklistMode(items, dismissed);
  return <div className="space-y-4">
    {mode === "hidden" ? null : <section aria-labelledby="setup-title" className="rounded-2xl border px-4 py-3 text-[13px] leading-5">
      <div className="flex items-center justify-between gap-3">
        {mode === "open" || open ? <h2 id="setup-title" className="font-medium">Finish setting up</h2>
          : <button type="button" aria-expanded={false} onClick={() => setOpen(true)} className="-ml-1 inline-flex items-center gap-1 rounded px-1 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"><ChevronRight className="size-3.5" aria-hidden="true" /><span id="setup-title">Optional setup · {items.length} {items.length === 1 ? "item" : "items"}</span></button>}
        <button type="button" aria-label="Dismiss setup list" onClick={dismiss} className="-mr-1.5 flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"><X className="size-3.5" aria-hidden="true" /></button>
      </div>
      {mode === "open" || open ? <ul className="mt-2 space-y-2.5 pb-1">{items.map((item) => <li key={item.id} className="flex gap-2.5">
        <Circle className={`mt-1 size-3 shrink-0 ${item.required ? "text-foreground" : "text-muted-foreground"}`} aria-hidden="true" />
        <div className="min-w-0"><p>{item.href ? <Link href={item.href} className={linkClass}>{item.label}</Link> : item.label}{item.required ? <span className="text-muted-foreground"> · required</span> : null}</p><p className="break-words text-muted-foreground">{item.detail}</p></div>
      </li>)}</ul> : null}
    </section>}
  </div>;
}
