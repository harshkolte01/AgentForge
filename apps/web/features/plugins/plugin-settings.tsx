"use client";
import { useId, useState, type FormEvent, type ReactNode } from "react";
import { ChevronDown, CircleAlert, ExternalLink, KeyRound, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { pluginsStore, usePlugins } from "@/features/plugins/use-plugins";
import { focusClass, sectionClass, SectionHeader, StatusDot, subheadingClass, subsectionClass } from "@/features/settings/settings-ui";
import {
  changePlugin, connectPlugin, MANY_TOOLS, refreshPlugin, removePlugin, serverHost, toolSummary,
  type PluginCatalogEntry, type PluginConnection, type PluginsOverview,
} from "@/lib/plugins-api";
import { message } from "@/lib/task-api";

const linkClass = `rounded underline underline-offset-4 ${focusClass}`;
const NO_KEY = "Saving a token needs an encryption key on the server. Ask the operator to set PLUGIN_ENCRYPTION_KEY.";

function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
  return <div className="space-y-1.5"><label htmlFor={id} className="text-sm font-medium">{label}</label>{children}{hint ? <p id={`${id}-hint`} className="text-xs leading-5 text-muted-foreground">{hint}</p> : null}</div>;
}

/** Connects a catalog plugin (a token, when it takes one) or a server of the person's own. */
function ConnectForm({ entry, credentials, onClose }: { entry: PluginCatalogEntry | null; credentials: boolean; onClose: () => void }) {
  const id = useId();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [header, setHeader] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const takesToken = entry ? entry.auth !== "none" : true;
  const needsToken = entry?.auth === "required";
  const tokenBlocked = takesToken && !credentials;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const connected = await connectPlugin(entry
        ? { plugin_key: entry.key, ...(token.trim() ? { token: token.trim() } : {}) }
        : { plugin_key: "custom", name: name.trim(), server_url: url.trim(), ...(token.trim() ? { token: token.trim() } : {}), ...(header.trim() ? { header_name: header.trim() } : {}) });
      pluginsStore.apply(connected);
      onClose();
    } catch (failure) { setError(message(failure)); setBusy(false); }
  }
  return <form onSubmit={submit} className="space-y-4 rounded-xl border bg-muted/30 p-4" aria-label={entry ? `Connect ${entry.name}` : "Connect your own plugin"} aria-busy={busy}>
    {!entry ? <>
      <Field id={`${id}-name`} label="Name"><input id={`${id}-name`} className={inputClass} value={name} onChange={(event) => setName(event.target.value)} required maxLength={60} placeholder="My tools" disabled={busy} autoComplete="off" /></Field>
      <Field id={`${id}-url`} label="Server address" hint="The address of a remote MCP server (Streamable HTTP), for example https://example.com/mcp."><input id={`${id}-url`} aria-describedby={`${id}-url-hint`} className={inputClass} type="url" value={url} onChange={(event) => setUrl(event.target.value)} required maxLength={2048} placeholder="https://example.com/mcp" disabled={busy} autoComplete="off" autoCapitalize="none" spellCheck={false} /></Field>
    </> : null}
    {takesToken ? <Field id={`${id}-token`} label={`${entry?.token_label ?? "Access token"}${needsToken ? "" : " (optional)"}`}
      hint={tokenBlocked ? NO_KEY : entry?.token_help || "Sent to the server with every request. Stored encrypted and never shown again."}>
      <input id={`${id}-token`} aria-describedby={`${id}-token-hint`} className={inputClass} type="password" value={token} onChange={(event) => setToken(event.target.value)} required={needsToken} maxLength={4096} disabled={busy || tokenBlocked} autoComplete="off" autoCapitalize="none" spellCheck={false} />
    </Field> : null}
    {!entry ? <details className="text-sm"><summary className={`cursor-pointer rounded text-muted-foreground ${focusClass}`}>Advanced</summary>
      <div className="pt-3"><Field id={`${id}-header`} label="Header name" hint="Where the token goes. Leave empty to send it as “Authorization: Bearer …”."><input id={`${id}-header`} aria-describedby={`${id}-header-hint`} className={inputClass} value={header} onChange={(event) => setHeader(event.target.value)} maxLength={64} placeholder="Authorization" disabled={busy} autoComplete="off" autoCapitalize="none" spellCheck={false} /></Field></div>
    </details> : null}
    <p className="text-xs leading-5 text-muted-foreground">{entry ? "" : "Only connect servers you trust: the agent sends them what each tool call needs. "}Nothing is saved unless the server answers and lists its tools.</p>
    {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
    <div className="flex flex-wrap justify-end gap-2">
      <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" onClick={onClose} disabled={busy}>Cancel</Button>
      <Button type="submit" className="min-h-10 sm:min-h-8" disabled={busy || (needsToken && tokenBlocked)}>{busy ? "Connecting…" : "Connect"}</Button>
    </div>
  </form>;
}

function Switch({ checked, label, disabled, onChange }: { checked: boolean; label: string; disabled?: boolean; onChange: (next: boolean) => void }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} title={label} disabled={disabled} onClick={() => onChange(!checked)}
    className={`relative inline-flex h-6 w-10 shrink-0 items-center rounded-full border transition-colors disabled:opacity-50 motion-reduce:transition-none ${focusClass} ${checked ? "border-transparent bg-primary" : "bg-muted"}`}>
    <span aria-hidden="true" className={`inline-block size-4 rounded-full bg-background shadow transition-transform motion-reduce:transition-none ${checked ? "translate-x-5" : "translate-x-1"}`} />
  </button>;
}

function ToolList({ connection, busy, onToggle }: { connection: PluginConnection; busy: boolean; onToggle: (name: string, on: boolean) => void }) {
  return <div className="space-y-2 border-t pt-3">
    {connection.tool_count > MANY_TOOLS ? <p className="text-xs leading-5 text-muted-foreground">This plugin offers {connection.tool_count} tools. Each one the agent can use adds to every request, so switch off the ones you do not need.</p> : null}
    <ul className="divide-y rounded-lg border">{connection.tools.map((tool) => <li key={tool.name} className="flex items-start gap-3 px-3 py-2.5">
      <input type="checkbox" className="mt-1 size-4 shrink-0 accent-primary" checked={tool.enabled} disabled={busy} onChange={(event) => onToggle(tool.name, event.target.checked)} aria-label={`Allow ${tool.title || tool.name}`} id={`tool-${connection.id}-${tool.name}`} />
      <label htmlFor={`tool-${connection.id}-${tool.name}`} className="min-w-0 flex-1 cursor-pointer space-y-0.5">
        <span className="flex flex-wrap items-center gap-x-2 text-sm"><span className="break-all font-medium">{tool.title || tool.name}</span>
          <span className="text-xs text-muted-foreground">{tool.read_only ? "Only reads" : "May change things"}</span></span>
        {tool.description ? <span className="line-clamp-2 block text-xs leading-5 text-muted-foreground">{tool.description}</span> : null}
      </label>
    </li>)}</ul>
  </div>;
}

function Connection({ connection, entry, credentials }: { connection: PluginConnection; entry: PluginCatalogEntry | undefined; credentials: boolean }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"" | "save" | "refresh" | "remove">("");
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [token, setToken] = useState("");
  const canHoldToken = !entry || entry.auth !== "none";
  async function run<T>(kind: "save" | "refresh" | "remove", action: () => Promise<T>): Promise<T | undefined> {
    setBusy(kind); setError("");
    try { return await action(); }
    catch (failure) { setError(message(failure)); return undefined; }
    finally { setBusy(""); }
  }
  const change = async (patch: Parameters<typeof changePlugin>[1]) => {
    const updated = await run("save", () => changePlugin(connection.id, patch));
    if (updated) pluginsStore.apply(updated);
    return !!updated;
  };
  async function replaceToken(event: FormEvent) {
    event.preventDefault();
    if (await change({ token: token.trim() })) { setToken(""); setReplacing(false); }
  }
  async function refresh() {
    const updated = await run("refresh", () => refreshPlugin(connection.id));
    if (updated) pluginsStore.apply(updated);
  }
  async function remove() {
    const done = await run("remove", async () => { await removePlugin(connection.id); return true; });
    if (done) pluginsStore.remove(connection.id);
    else setConfirming(false);
  }
  const failing = connection.status === "error";
  const actionClass = `inline-flex min-h-10 items-center gap-1.5 rounded-md px-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50 sm:min-h-8 ${focusClass}`;
  return <li className="space-y-3 py-4" data-plugin={connection.plugin_key}>
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <p className="flex items-center gap-2 text-sm font-medium"><StatusDot tone={failing ? "warn" : "ok"} /><span className="break-words">{connection.name}</span></p>
        <p className="break-words text-xs leading-5 text-muted-foreground">{serverHost(connection.server_url)} · {toolSummary(connection)}{connection.has_credential ? " · token saved" : ""}</p>
        {failing ? <p role="status" className="flex items-start gap-1.5 text-xs leading-5 text-destructive"><CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /><span>{connection.last_error ?? "The server could not be reached."} Refresh to try again{canHoldToken ? ", or replace the token" : ""}.</span></p> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2 pt-0.5"><span id={`${id}-default`} className="hidden text-xs text-muted-foreground sm:inline">New chats</span>
        <Switch checked={connection.enabled} label={`${connection.name}: use in new conversations`} disabled={busy !== ""} onChange={(next) => void change({ enabled: next })} /></div>
    </div>
    <div className="-ml-2 flex flex-wrap items-center gap-x-1">
      <button type="button" className={actionClass} aria-expanded={open} aria-controls={`${id}-tools`} onClick={() => setOpen((value) => !value)}><ChevronDown className={`size-4 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`} aria-hidden="true" />Tools</button>
      <button type="button" className={actionClass} disabled={busy !== ""} onClick={() => void refresh()}><RefreshCw className={`size-3.5 ${busy === "refresh" ? "motion-safe:animate-spin" : ""}`} aria-hidden="true" />{busy === "refresh" ? "Refreshing…" : "Refresh"}</button>
      {canHoldToken ? <button type="button" className={actionClass} disabled={busy !== ""} aria-expanded={replacing} onClick={() => setReplacing((value) => !value)}><KeyRound className="size-3.5" aria-hidden="true" />{connection.has_credential ? "Replace token" : "Add token"}</button> : null}
      {entry?.docs_url ? <a href={entry.docs_url} target="_blank" rel="noopener noreferrer" className={actionClass}><ExternalLink className="size-3.5" aria-hidden="true" />About<span className="sr-only"> {connection.name} (opens in a new tab)</span></a> : null}
      <span className="flex-1" />
      {confirming ? <span className="flex items-center gap-1 text-sm"><span className="hidden text-muted-foreground sm:inline">Remove {connection.name}?</span>
        <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" onClick={() => setConfirming(false)} disabled={busy !== ""}>Keep</Button>
        <Button type="button" variant="destructive" className="min-h-10 sm:min-h-8" onClick={() => void remove()} disabled={busy !== ""}>{busy === "remove" ? "Removing…" : "Remove"}</Button></span>
        : <button type="button" className={actionClass} disabled={busy !== ""} onClick={() => setConfirming(true)}><Trash2 className="size-3.5" aria-hidden="true" />Remove</button>}
    </div>
    {replacing ? <form onSubmit={replaceToken} className="flex flex-wrap items-end gap-2" aria-label={`New token for ${connection.name}`}>
      <div className="min-w-0 flex-1 basis-56 space-y-1.5"><label htmlFor={`${id}-token`} className="text-sm font-medium">{entry?.token_label ?? "Access token"}</label>
        <input id={`${id}-token`} className={inputClass} type="password" value={token} onChange={(event) => setToken(event.target.value)} required maxLength={4096} autoComplete="off" autoCapitalize="none" spellCheck={false} disabled={busy !== "" || !credentials} aria-describedby={credentials ? undefined : `${id}-nokey`} /></div>
      <Button type="submit" className="min-h-10 sm:min-h-8" disabled={busy !== "" || !credentials || !token.trim()}>{busy === "save" ? "Checking…" : "Save token"}</Button>
      {!credentials ? <p id={`${id}-nokey`} className="basis-full text-xs leading-5 text-muted-foreground">{NO_KEY}</p> : null}
    </form> : null}
    {open ? <div id={`${id}-tools`}><ToolList connection={connection} busy={busy !== ""}
      onToggle={(name, on) => {
        const off = new Set(connection.tools.filter((tool) => !tool.enabled).map((tool) => tool.name));
        if (on) off.delete(name); else off.add(name);
        void change({ disabled_tools: [...off] });
      }} /></div> : null}
    {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
  </li>;
}

function Catalog({ overview }: { overview: PluginsOverview }) {
  const [opened, setOpened] = useState<string | null>(null);
  const connected = new Set(overview.connections.map((connection) => connection.plugin_key));
  const available = overview.catalog.filter((entry) => !connected.has(entry.key));
  const full = overview.connections.length >= overview.max_connections;
  return <div className={subsectionClass}>
    <h3 className={subheadingClass}>Add a plugin</h3>
    {full ? <p className="text-sm leading-6 text-muted-foreground">You have connected the most plugins allowed ({overview.max_connections}). Remove one to add another.</p> : null}
    <ul className="divide-y rounded-xl border">
      {available.map((entry) => <li key={entry.key} className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1"><p className="flex flex-wrap items-center gap-x-2 text-sm font-medium">{entry.name}<span className="rounded-full border px-1.5 text-[10px] font-medium uppercase leading-4 tracking-wide text-muted-foreground">{entry.category}</span></p>
            <p className="text-sm leading-6 text-muted-foreground">{entry.description}</p>
            <p className="text-xs text-muted-foreground">{entry.auth === "none" ? "No account needed" : entry.auth === "optional" ? "Works without a token" : `Requires: ${entry.token_label}`}</p></div>
          {opened !== entry.key ? <Button type="button" variant="outline" className="min-h-10 shrink-0 sm:min-h-8" disabled={full} onClick={() => setOpened(entry.key)}>Connect</Button> : null}
        </div>
        {opened === entry.key ? <ConnectForm entry={entry} credentials={overview.credentials} onClose={() => setOpened(null)} /> : null}
      </li>)}
      <li className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1"><p className="text-sm font-medium">Your own server</p><p className="text-sm leading-6 text-muted-foreground">Connect any remote MCP server by its address.</p></div>
          {opened !== "custom" ? <Button type="button" variant="outline" className="min-h-10 shrink-0 sm:min-h-8" disabled={full} onClick={() => setOpened("custom")}>Add</Button> : null}
        </div>
        {opened === "custom" ? <ConnectForm entry={null} credentials={overview.credentials} onClose={() => setOpened(null)} /> : null}
      </li>
    </ul>
  </div>;
}

export function PluginSettings() {
  const { status, overview, error } = usePlugins();
  return <section className={sectionClass} aria-labelledby="plugins-title" aria-busy={status === "loading" && !overview}>
    <SectionHeader id="plugins-title" title="Plugins" description="Connect services so the agent can use their tools. Choose which ones a conversation may use from the plug icon beside the message box."
      action={<Button variant="ghost" size="icon" aria-label="Refresh plugins" disabled={status === "loading"} onClick={() => pluginsStore.reload()}><RefreshCw className={status === "loading" && overview ? "motion-safe:animate-spin" : ""} /></Button>} />
    {error && !overview ? <p role="alert" className="text-sm text-destructive">{error} <button type="button" className={linkClass} onClick={() => pluginsStore.reload()}>Try again</button></p> : null}
    {!overview && !error ? <div role="status" className="space-y-4 border-y py-5"><span className="sr-only">Loading plugins…</span><div className="h-5 w-48 animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-4 w-full max-w-md animate-pulse rounded bg-muted motion-reduce:animate-none" /></div> : null}
    {overview && !overview.enabled ? <p role="status" className="rounded-lg border px-3 py-2 text-sm leading-6">Plugins are turned off on this server. The operator can turn them on with PLUGINS_ENABLED.</p> : null}
    {overview?.enabled ? <>
      <div className="space-y-3">
        <h3 className={subheadingClass}>Connected{overview.connections.length ? <span className="ml-1.5 font-normal text-muted-foreground">{overview.connections.length}</span> : null}</h3>
        {overview.connections.length ? <ul className="divide-y border-y">{overview.connections.map((connection) => <Connection key={connection.id} connection={connection} entry={overview.catalog.find((item) => item.key === connection.plugin_key)} credentials={overview.credentials} />)}</ul>
          : <p className="text-sm leading-6 text-muted-foreground">No plugins are connected yet.</p>}
        <p className="max-w-prose text-xs leading-5 text-muted-foreground">Plugins are other people’s services: the agent sends them what a tool call needs, and what they return is treated as data, not instructions. In Ask me mode, a tool that may change something waits for your approval. New conversations start with the plugins that are on.</p>
      </div>
      <Catalog overview={overview} />
    </> : null}
  </section>;
}
