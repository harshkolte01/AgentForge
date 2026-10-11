"use client";
import { useEffect, useState, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { rowsClass, sectionClass, SectionHeader, SkeletonRows, StatusDot, subheadingClass, subsectionClass } from "@/features/settings/settings-ui";
import { api, formatDate, message } from "@/lib/task-api";
import type { WorkspaceConfiguration } from "@/lib/workspace-api";

type CheckResult = { status: "ok" | "error" | "not_configured" | "not_observed"; detail?: string | null };
type ReadyResponse = { status: "ready" | "degraded"; checks?: { database?: CheckResult } };
/** The API's own probes, read through the `/api` rewrite. `null` fields: that probe could not be reached. */
type Health = { live: boolean; ready: ReadyResponse | null };

async function readHealth(signal: AbortSignal): Promise<Health> {
  const probe = async <T,>(path: string) => {
    try {
      const response = await fetch(path, { cache: "no-store", signal });
      // A degraded service answers 503 with the same body: still worth reading.
      return await response.json() as T;
    } catch (failure) { if (signal.aborted) throw failure; return null; }
  };
  const [live, ready] = await Promise.all([probe<{ status?: string }>("/api/v1/health/live"), probe<ReadyResponse>("/api/v1/health/ready")]);
  return { live: live?.status === "ok", ready: ready && (ready.status === "ready" || ready.status === "degraded") ? ready : null };
}

/** "just now", "5 min ago", "3 h ago", else the date. */
export function relativeTime(value: string, now = Date.now()): string {
  const seconds = Math.round((now - new Date(value).getTime()) / 1000);
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return formatDate(value);
}

function Missing({ names }: { names: readonly string[] }) {
  return <>Missing {names.map((name, index) => <span key={name}>{index ? ", " : ""}<code className="rounded bg-muted px-1 py-0.5 font-mono text-[12px]">{name}</code></span>)}</>;
}

function Capability({ label, ready, missing }: { label: string; ready: boolean; missing?: readonly string[] }) {
  return <div className="contents">
    <dt className="text-muted-foreground">{label}</dt>
    <dd className="flex min-w-0 items-baseline gap-2"><span className="relative top-px"><StatusDot tone={ready ? "ok" : "off"} /></span><span className="min-w-0 break-words">{ready ? "Ready" : missing?.length ? <Missing names={missing} /> : "Not configured"}</span></dd>
  </div>;
}

function Row({ label, tone, children }: { label: string; tone: "ok" | "warn" | "off"; children: ReactNode }) {
  return <div className="contents"><dt className="text-muted-foreground">{label}</dt><dd className="flex min-w-0 items-baseline gap-2"><span className="relative top-px"><StatusDot tone={tone} /></span><span className="min-w-0 break-words">{children}</span></dd></div>;
}

export function StatusSettings() {
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    void Promise.allSettled([api<WorkspaceConfiguration>("/configuration", { signal }), readHealth(signal)]).then(([configuration, probes]) => {
      if (signal.aborted) return;
      if (configuration.status === "fulfilled") { setConfig(configuration.value); setError(""); } else setError(message(configuration.reason));
      setHealth(probes.status === "fulfilled" ? probes.value : { live: false, ready: null });
      setLoading(false);
    });
    return () => controller.abort();
  }, [refresh]);
  const worker = config?.worker;
  const database = health?.ready?.checks?.database;
  return <section className={sectionClass} aria-labelledby="status-title" aria-busy={loading}>
    <SectionHeader id="status-title" title="Status" description="What this workspace is set up to do, and whether its services are running." action={<Button variant="ghost" size="icon" disabled={loading} aria-label="Refresh status" onClick={() => { setLoading(true); setRefresh((value) => value + 1); }}><RefreshCw className="size-4" /></Button>} />
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {!config && !error ? <SkeletonRows count={5} label="Loading status…" /> : config ? <dl className={rowsClass}>
      <Capability label="General tasks" ready={!!config.general_configured} missing={config.general_missing_settings} />
      <Capability label="Research" ready={config.research_configured} missing={config.missing_settings} />
      {config.web_research_configured !== undefined ? <Capability label="Web research" ready={config.web_research_configured} /> : null}
      <Capability label="Coding sandbox" ready={config.coding_configured} missing={config.coding_missing_settings} />
      {config.github_writes_enabled !== undefined ? <Row label="GitHub publishing" tone={config.github_writes_enabled ? "ok" : "off"}>{config.github_writes_enabled ? "Enabled" : "Off"}</Row> : null}
      <Row label="Tracing" tone={config.tracing_configured ? "ok" : "off"}>{config.tracing_configured ? "Ready" : "Not configured"}</Row>
    </dl> : null}
    <div className={subsectionClass}>
      <h3 className={subheadingClass}>Services</h3>
      {!health ? <SkeletonRows count={3} label="Checking services…" /> : <dl className={rowsClass}>
        <Row label="API" tone={health.live ? "ok" : "warn"}>{health.live ? "Running" : "Unreachable"}</Row>
        <Row label="Database" tone={database?.status === "ok" ? "ok" : "warn"}>{database?.status === "ok" ? "Ready" : !health.ready ? "Unknown" : database?.detail === "database_unreachable" ? "Unreachable" : "Degraded"}</Row>
        {worker ? <Row label="Worker" tone={worker.status === "online" ? "ok" : "warn"}>{worker.status === "online" ? "Online" : "Offline"}{worker.last_seen_at ? <span className="text-muted-foreground"> · last seen {relativeTime(worker.last_seen_at)}</span> : null}</Row> : null}
      </dl>}
    </div>
    {config?.qualification ? <p className="max-w-prose border-t pt-5 text-xs leading-5 text-muted-foreground">{config.qualification}</p> : null}
  </section>;
}
