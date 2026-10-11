"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useSyncExternalStore } from "react";
import { ArrowLeft, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWorkspaceIdentity } from "@/components/session-boundary";
import { AgentSettings } from "@/features/settings/agent-settings";
import { AppearanceSettings } from "@/features/settings/appearance-settings";
import { ConversationSettings } from "@/features/settings/conversation-settings";
import { focusClass, rowsClass, sectionClass, SectionHeader, subsectionClass } from "@/features/settings/settings-ui";
import { settingsSections, type SettingsSection } from "@/features/settings/settings-sections";
import { StatusSettings } from "@/features/settings/status-settings";
import { lastChat, routes } from "@/lib/routes";
import { useDocumentTitle } from "@/lib/use-document-title";

/** A quiet placeholder while a section's code loads: a header-shaped block, never a line of "Loading…" text. */
function SectionPlaceholder() {
  return <div role="status" className={`${sectionClass} animate-pulse motion-reduce:animate-none`}><span className="sr-only">Loading…</span><div className="space-y-2" aria-hidden="true"><div className="h-7 w-32 rounded bg-muted" /><div className="h-5 w-72 max-w-full rounded bg-muted" /></div></div>;
}
const GitHubConnection = dynamic(() => import("@/features/github/github-connection").then((module) => module.GitHubConnection), { loading: SectionPlaceholder });
const AgentManager = dynamic(() => import("@/features/agents/agent-manager").then((module) => module.AgentManager), { loading: SectionPlaceholder });

const noSubscription = () => () => {};

function AccountSettings() {
  const identity = useWorkspaceIdentity();
  return <section className={sectionClass} aria-labelledby="account-title">
    <SectionHeader id="account-title" title="Account" description="Your current account and sign-in method." />
    <dl className={rowsClass}><dt className="text-muted-foreground">Name</dt><dd className="break-words">{identity?.session.user?.display_name ?? "Workspace user"}</dd><dt className="text-muted-foreground">Sign-in method</dt><dd>{identity?.session.mode === "github_app" ? "GitHub" : "Local access"}</dd></dl>
    <div className={subsectionClass}><Button variant="outline" disabled={!identity || identity.busy} onClick={() => void identity?.logout()}><LogOut className="size-4" />{identity?.busy ? "Signing out…" : "Sign out"}</Button></div>
  </section>;
}

export function SettingsWorkspace({ section, initialCreate = false, agentId }: { section: SettingsSection; initialCreate?: boolean; agentId?: string }) {
  // The conversation this tab came from (read after hydration: the server has no tab to ask).
  const back = useSyncExternalStore(noSubscription, lastChat, () => routes.newConversation);
  const label = settingsSections.find((item) => item.id === section)?.label ?? "Account";
  useDocumentTitle(`${label} · Settings · AgentForge`);
  return <div className="min-h-svh bg-background">
    <header className="mx-auto flex min-h-16 max-w-5xl items-center justify-between gap-4 px-5 sm:px-8"><Link href={back} className={`inline-flex min-h-10 items-center gap-2 rounded-md text-sm text-muted-foreground hover:text-foreground ${focusClass}`}><ArrowLeft className="size-4" aria-hidden="true" />Back to chat</Link><span className="text-sm font-semibold">AgentForge</span></header>
    <main className="mx-auto max-w-5xl px-5 pb-16 pt-5 sm:px-8 sm:pt-9"><h1 className="mb-7 text-2xl font-semibold tracking-tight">Settings</h1>
      <div className="grid gap-8 md:grid-cols-[11rem_minmax(0,1fr)] md:gap-12">
        <nav aria-label="Settings" className="flex flex-wrap gap-1 border-b pb-4 md:flex-col md:self-start md:border-0 md:pb-0">{settingsSections.map((item) => <Link key={item.id} href={routes.settings(item.id)} aria-current={section === item.id ? "page" : undefined} className={`flex min-h-10 items-center gap-2 rounded-lg px-3 py-2.5 text-sm transition-colors motion-reduce:transition-none ${focusClass} ${item.labs ? "md:mt-3" : ""} ${section === item.id ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}>{item.label}{item.labs ? <span className="rounded-full border px-1.5 text-[10px] font-medium uppercase leading-4 tracking-wide text-muted-foreground">Labs</span> : null}</Link>)}</nav>
        <div className="min-w-0">{section === "account" ? <AccountSettings />
          : section === "agent" ? <AgentSettings />
            : section === "status" ? <StatusSettings />
              : section === "github" ? <div className={sectionClass}><GitHubConnection embedded /></div>
                : section === "conversations" ? <ConversationSettings />
                  : section === "appearance" ? <AppearanceSettings />
                    : <AgentManager key={`${agentId ?? "list"}-${initialCreate ? "new" : "view"}`} initialCreate={initialCreate} initialAgentId={agentId} embedded />}</div>
      </div>
    </main>
  </div>;
}
