"use client";
import Link from "next/link";
import { McpMark } from "@/features/plugins/plugin-logo";
import { PluginsPanel } from "@/features/plugins/plugins-panel";
import { pluginsTabs, type OAuthResult, type PluginsTab } from "@/features/plugins/plugins-tabs";
import { focusClass } from "@/features/settings/settings-ui";
import { SkillsPanel } from "@/features/skills/skills-panel";
import { routes } from "@/lib/routes";

/** The Plugins page, shown in the workspace beside the conversations: connected services (MCP plugins) and skills, one tab each. */
export function PluginsPage({ tab, result = null }: { tab: PluginsTab; result?: OAuthResult | null }) {
  const tabNav = <nav aria-label="Plugins and skills" className="flex gap-1">
    {pluginsTabs.map((item) => {
      const active = tab === item.id;
      return <Link key={item.id} href={routes.plugins(item.id === "plugins" ? undefined : item.id)} aria-current={active ? "page" : undefined} scroll={false}
        className={`inline-flex h-10 items-center rounded-full px-5 text-sm transition-colors motion-reduce:transition-none ${focusClass} ${active ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground"}`}>{item.label}</Link>;
    })}
  </nav>;
  return <div className="mx-auto w-full max-w-5xl px-5 pb-20 pt-4 sm:px-8 lg:pt-8">
    <header className="mb-8 space-y-2">
      <h1 className="text-3xl font-semibold tracking-tight">Plugins &amp; skills</h1>
      <p className="flex max-w-prose items-start gap-2 text-[0.9375rem] leading-6 text-muted-foreground">{tab === "plugins"
        ? <><McpMark className="mt-1 size-4 shrink-0" /><span>Connect services so the agent can use their tools. Plugins speak MCP, the open Model Context Protocol, so any MCP server can be added.</span></>
        : <span>Skills are playbooks the agent follows for a kind of work. It loads one when your request fits, or when you start a message with <code className="rounded bg-muted px-1 font-mono text-[0.85em]">/skill-name</code>.</span>}</p>
    </header>
    {tab === "plugins" ? <PluginsPanel tabs={tabNav} result={result} /> : <SkillsPanel tabs={tabNav} />}
  </div>;
}
