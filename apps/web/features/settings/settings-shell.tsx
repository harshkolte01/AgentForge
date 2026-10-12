"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useEffect, useRef, useSyncExternalStore, type ComponentType, type ReactNode } from "react";
import { Archive, ArrowLeft, Bot, LogOut, Palette, Sparkles, UserRound } from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { GitHubMark } from "@/components/github-mark";
import { Button } from "@/components/ui/button";
import { useWorkspaceIdentity } from "@/components/session-boundary";
import { AgentSettings } from "@/features/settings/agent-settings";
import { AppearanceSettings } from "@/features/settings/appearance-settings";
import { ConversationSettings } from "@/features/settings/conversation-settings";
import { cardClass, Fact, Facts, focusClass, Group, rowClass, sectionClass, SectionHeader } from "@/features/settings/settings-ui";
import { settingsSections, type SettingsSection } from "@/features/settings/settings-sections";
import { lastChat, routes } from "@/lib/routes";
import { useDocumentTitle } from "@/lib/use-document-title";

/** A quiet placeholder while a section's code loads: a header-shaped block, never a line of "Loading…" text. */
function SectionPlaceholder() {
  return <div role="status" className={`${sectionClass} animate-pulse motion-reduce:animate-none`}><span className="sr-only">Loading…</span><div className="space-y-2" aria-hidden="true"><div className="h-7 w-32 rounded bg-muted" /><div className="h-5 w-72 max-w-full rounded bg-muted" /></div></div>;
}
const GitHubConnection = dynamic(() => import("@/features/github/github-connection").then((module) => module.GitHubConnection), { loading: SectionPlaceholder });
const AgentManager = dynamic(() => import("@/features/agents/agent-manager").then((module) => module.AgentManager), { loading: SectionPlaceholder });

const noSubscription = () => () => {};

const sectionIcons: Record<SettingsSection, ComponentType<{ className?: string }>> = {
  account: UserRound,
  agent: Sparkles,
  github: GitHubMark,
  conversations: Archive,
  appearance: Palette,
  agents: Bot,
};

function AccountSettings() {
  const identity = useWorkspaceIdentity();
  const name = identity?.session.user?.display_name ?? "Workspace user";
  const github = identity?.session.mode === "github_app";
  return <section className={sectionClass} aria-labelledby="account-title">
    <SectionHeader id="account-title" title="Account" description="Your current account and sign-in method." />
    <Facts>
      <Fact label="Name"><span className="flex items-center gap-3"><span aria-hidden="true" className="grid size-8 shrink-0 select-none place-items-center rounded-full bg-primary text-sm font-medium uppercase text-primary-foreground">{[...name][0] ?? "?"}</span><span className="min-w-0 break-words font-medium">{name}</span></span></Fact>
      <Fact label="Sign-in method"><span className="inline-flex items-center gap-2">{github ? <GitHubMark className="size-4 text-muted-foreground" /> : null}{github ? "GitHub" : "Local access"}</span></Fact>
    </Facts>
    <Group title="Session">
      <div className={`${cardClass} ${rowClass}`}>
        <div className="min-w-0 space-y-0.5"><p className="text-sm font-medium">Sign out</p><p className="text-[0.8125rem] leading-5 text-muted-foreground">End your session in this browser.</p></div>
        <Button variant="outline" disabled={!identity || identity.busy} onClick={() => void identity?.logout()}><LogOut className="size-4" />{identity?.busy ? "Signing out…" : "Sign out"}</Button>
      </div>
    </Group>
  </section>;
}

/** The settings list: a column down the left of the page, one scrolling row of pills on a narrow screen. */
function SettingsNav({ section }: { section: SettingsSection }) {
  const nav = useRef<HTMLElement>(null);
  // On a narrow screen the row scrolls sideways: bring the open section into view (without moving the page itself).
  useEffect(() => {
    const row = nav.current;
    const current = row?.querySelector<HTMLElement>("[aria-current='page']");
    if (!row || !current) return;
    const place = current.getBoundingClientRect(), frame = row.getBoundingClientRect();
    row.scrollLeft += place.left - frame.left - (row.clientWidth - place.width) / 2;
  }, [section]);
  const items: ReactNode[] = [];
  for (const item of settingsSections) {
    const Icon = sectionIcons[item.id];
    const active = section === item.id;
    if (item.labs) items.push(<span key={`${item.id}-rule`} role="presentation" className="mx-3 my-2 hidden border-t md:block" />);
    items.push(<Link key={item.id} href={routes.settings(item.id)} aria-current={active ? "page" : undefined}
      className={`flex min-h-10 shrink-0 items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition-colors motion-reduce:transition-none ${focusClass} ${active ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}>
      <Icon className={`size-4 shrink-0 ${active ? "text-foreground" : ""}`} />{item.label}
      {item.labs ? <span className="rounded-full border px-1.5 text-[10px] font-medium uppercase leading-4 tracking-wide text-muted-foreground">Labs</span> : null}
    </Link>);
  }
  return <nav ref={nav} aria-label="Settings" className="-mx-5 flex gap-1 overflow-x-auto px-5 py-1 [scrollbar-width:none] sm:-mx-8 sm:px-8 md:sticky md:top-6 md:mx-0 md:flex-col md:self-start md:overflow-visible md:px-0 md:py-0 [&::-webkit-scrollbar]:hidden">{items}</nav>;
}

export function SettingsWorkspace({ section, initialCreate = false, agentId }: { section: SettingsSection; initialCreate?: boolean; agentId?: string }) {
  // The conversation this tab came from (read after hydration: the server has no tab to ask).
  const back = useSyncExternalStore(noSubscription, lastChat, () => routes.newConversation);
  const label = settingsSections.find((item) => item.id === section)?.label ?? "Account";
  useDocumentTitle(`${label} · Settings · AgentForge`);
  return <div className="min-h-svh bg-background">
    <header className="mx-auto flex min-h-16 max-w-5xl items-center justify-between gap-4 px-5 sm:px-8"><Link href={back} className={`group inline-flex min-h-10 items-center gap-2 rounded-md text-sm text-muted-foreground transition-colors hover:text-foreground motion-reduce:transition-none ${focusClass}`}><ArrowLeft className="size-4 transition-transform group-hover:-translate-x-0.5 motion-reduce:transition-none" aria-hidden="true" />Back to chat</Link><span className="flex items-center gap-2 text-sm font-semibold"><BrandMark className="size-5" />AgentForge</span></header>
    <main className="mx-auto max-w-5xl px-5 pb-20 pt-4 sm:px-8 sm:pt-8"><h1 className="mb-6 text-3xl font-semibold tracking-tight sm:mb-9">Settings</h1>
      <div className="grid gap-6 md:grid-cols-[13rem_minmax(0,1fr)] md:gap-14">
        <SettingsNav section={section} />
        <div className="min-w-0">{section === "account" ? <AccountSettings />
          : section === "agent" ? <AgentSettings />
              : section === "github" ? <div className={sectionClass}><GitHubConnection embedded /></div>
                : section === "conversations" ? <ConversationSettings />
                  : section === "appearance" ? <AppearanceSettings />
                    : <div className={sectionClass}><AgentManager key={`${agentId ?? "list"}-${initialCreate ? "new" : "view"}`} initialCreate={initialCreate} initialAgentId={agentId} embedded /></div>}</div>
      </div>
    </main>
  </div>;
}
