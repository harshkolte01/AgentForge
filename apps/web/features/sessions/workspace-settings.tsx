"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useWorkspaceIdentity } from "@/components/session-boundary";
import { api, message } from "@/lib/task-api";
import type { WorkspaceConfiguration } from "@/lib/workspace-api";

export function WorkspaceSettings() {
  const identity = useWorkspaceIdentity();
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<WorkspaceConfiguration>("/configuration", { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) { setConfig(value); setError(""); }
    }).catch((error) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [refresh]);
  return <section className="mx-auto max-w-2xl space-y-9 py-4" aria-labelledby="settings-title"><header className="space-y-2"><h1 id="settings-title" className="text-2xl font-semibold tracking-tight">Workspace settings</h1><p className="text-sm leading-6 text-muted-foreground">Your account and the runtime AgentForge manages for you. Cost and time limits are applied automatically to every message.</p></header>
    <div className="space-y-4 border-t pt-5"><h2 className="text-sm font-semibold">Account</h2><dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 text-sm"><dt className="text-muted-foreground">Name</dt><dd className="break-words">{identity?.session.user?.display_name ?? "Workspace user"}</dd><dt className="text-muted-foreground">Access</dt><dd>{identity?.session.mode === "github_app" ? "GitHub account" : "Local access"}</dd></dl><Link href="/connections" className="inline-flex min-h-9 items-center text-sm font-medium underline underline-offset-4 focus-visible:outline-2">Manage GitHub connection</Link></div>
    {config ? <div className="space-y-4 border-t pt-5"><h2 className="text-sm font-semibold">Runtime</h2><dl className="grid gap-x-5 gap-y-3 text-sm sm:grid-cols-[9rem_minmax(0,1fr)]"><dt className="text-muted-foreground">Model</dt><dd className="break-all">{config.configured_model ?? "Not configured"}</dd><dt className="text-muted-foreground">Provider</dt><dd>{config.configured_provider ?? "Not configured"}</dd><dt className="text-muted-foreground">Per-task limit</dt><dd className="tabular-nums">{config.per_task_cost_limit_usd == null ? "Not reported" : `$${config.per_task_cost_limit_usd}`}</dd><dt className="text-muted-foreground">Research</dt><dd>{config.research_configured ? "Configured" : "Setup incomplete"}</dd><dt className="text-muted-foreground">General tasks</dt><dd>{config.general_configured ? "Configured" : "Setup incomplete"}</dd><dt className="text-muted-foreground">Sandbox coding</dt><dd>{config.coding_configured ? "Configured" : "Setup incomplete"}</dd><dt className="text-muted-foreground">Tracing</dt><dd>{config.tracing_configured ? "Configured" : "Not configured"}</dd></dl><p className="text-xs leading-5 text-muted-foreground">{config.qualification}</p></div> : !error ? <p role="status" className="text-sm text-muted-foreground">Loading runtime configuration…</p> : null}
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}<Button variant="outline" onClick={() => setRefresh((value) => value + 1)}>Refresh configuration</Button>
  </section>;
}
