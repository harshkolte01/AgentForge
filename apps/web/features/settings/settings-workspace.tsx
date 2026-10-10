"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { ArrowLeft, LogOut, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWorkspaceIdentity } from "@/components/session-boundary";
import { api, message } from "@/lib/task-api";
import type { WorkspaceConfiguration } from "@/lib/workspace-api";

export type SettingsSection = "account" | "runtime" | "github" | "agents";
const sections = [{ id: "account", label: "Account" }, { id: "runtime", label: "Model & limits" }, { id: "github", label: "GitHub" }, { id: "agents", label: "Custom agents" }] as const;
const GitHubConnection = dynamic(() => import("@/features/github/github-connection").then((module) => module.GitHubConnection), { loading: () => <p role="status" className="text-sm text-muted-foreground">Loading GitHub settings…</p> });
const AgentManager = dynamic(() => import("@/features/agents/agent-manager").then((module) => module.AgentManager), { loading: () => <p role="status" className="text-sm text-muted-foreground">Loading custom agents…</p> });

function RuntimeSettings() {
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    void api<WorkspaceConfiguration>("/configuration", { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) { setConfig(value); setError(""); setLoading(false); }
    }).catch((error) => { if (!controller.signal.aborted) { setError(message(error)); setLoading(false); } });
    return () => controller.abort();
  }, [refresh]);
  return <section className="space-y-7" aria-labelledby="runtime-title" aria-busy={loading}>
    <header className="flex items-start justify-between gap-4"><div className="space-y-2"><h2 id="runtime-title" className="text-lg font-semibold">Model & limits</h2><p className="text-sm leading-6 text-muted-foreground">The model and limits configured for this workspace.</p></div><Button variant="ghost" size="icon" disabled={loading} aria-label="Refresh model configuration" onClick={() => { setLoading(true); setRefresh((value) => value + 1); }}><RefreshCw className="size-4" /></Button></header>
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {loading && !config ? <p role="status" className="text-sm text-muted-foreground">Loading configuration…</p> : config ? <>
      <dl className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-5 gap-y-4 text-sm"><dt className="text-muted-foreground">Model</dt><dd className="break-words">{config.configured_model ?? "Not configured"}</dd><dt className="text-muted-foreground">Provider</dt><dd className="break-words">{config.configured_provider ?? "Not configured"}</dd><dt className="text-muted-foreground">Per-task limit</dt><dd className="tabular-nums">{config.per_task_cost_limit_usd == null ? "Not reported" : `$${config.per_task_cost_limit_usd}`}</dd></dl>
      <div className="space-y-3 border-t pt-5"><h3 className="text-sm font-medium">Availability</h3><dl className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-5 gap-y-3 text-sm">{[["General tasks", config.general_configured], ["Research", config.research_configured], ["Sandbox coding", config.coding_configured], ["Tracing", config.tracing_configured]].map(([label, ready]) => <div key={String(label)} className="contents"><dt className="text-muted-foreground">{label}</dt><dd>{ready ? "Configured" : "Not configured"}</dd></div>)}</dl><p className="max-w-prose text-xs leading-5 text-muted-foreground">Availability reflects server configuration.</p></div>
    </> : null}
  </section>;
}

export function SettingsWorkspace({ section, initialCreate = false, agentId }: { section: SettingsSection; initialCreate?: boolean; agentId?: string }) {
  const identity = useWorkspaceIdentity();
  return <div className="min-h-svh bg-background">
    <header className="mx-auto flex min-h-16 max-w-5xl items-center justify-between gap-4 px-5 sm:px-8"><Link href="/workspace" className="inline-flex min-h-10 items-center gap-2 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-4"><ArrowLeft className="size-4" aria-hidden="true" />Back to chat</Link><span className="text-sm font-semibold">AgentForge</span></header>
    <main className="mx-auto max-w-5xl px-5 pb-16 pt-5 sm:px-8 sm:pt-9"><h1 className="mb-7 text-2xl font-semibold tracking-tight">Settings</h1>
      <div className="grid gap-8 md:grid-cols-[11rem_minmax(0,1fr)] md:gap-12"><nav aria-label="Settings" className="flex flex-wrap gap-1 border-b pb-4 md:flex-col md:self-start md:border-0 md:pb-0">{sections.map((item) => <Link key={item.id} href={`/settings?section=${item.id}`} aria-current={section === item.id ? "page" : undefined} className={`min-h-10 rounded-lg px-3 py-2.5 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 ${section === item.id ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}>{item.label}</Link>)}</nav>
        <div className="min-w-0">{section === "account" ? <section className="max-w-xl space-y-7" aria-labelledby="account-title"><header className="space-y-2"><h2 id="account-title" className="text-lg font-semibold">Account</h2><p className="text-sm leading-6 text-muted-foreground">Your current account and sign-in method.</p></header><dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-5 gap-y-4 text-sm"><dt className="text-muted-foreground">Name</dt><dd className="break-words">{identity?.session.user?.display_name ?? "Workspace user"}</dd><dt className="text-muted-foreground">Sign-in</dt><dd>{identity?.session.mode === "github_app" ? "GitHub" : "Local access"}</dd></dl><div className="border-t pt-5"><Button variant="outline" disabled={!identity || identity.busy} onClick={() => void identity?.logout()}><LogOut className="size-4" />{identity?.busy ? "Signing out…" : "Sign out"}</Button></div></section> : section === "runtime" ? <RuntimeSettings /> : section === "github" ? <GitHubConnection embedded /> : <AgentManager key={`${agentId ?? "list"}-${initialCreate ? "new" : "view"}`} initialCreate={initialCreate} initialAgentId={agentId} embedded />}</div>
      </div>
    </main>
  </div>;
}
