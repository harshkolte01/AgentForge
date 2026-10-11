"use client";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Check, CircleAlert, Copy, ExternalLink, KeyRound, LockKeyhole, Plus, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { inputClass } from "@/components/session-boundary";
import { AddMenu, EmptyLine, ListRow, ListSection, MenuItem, PlusButton, RefreshButton, RowList, RowSkeleton, SearchField, Toolbar } from "@/features/plugins/catalog-ui";
import { McpMark, PluginLogo } from "@/features/plugins/plugin-logo";
import type { OAuthResult } from "@/features/plugins/plugins-tabs";
import { pluginsStore, usePlugins } from "@/features/plugins/use-plugins";
import { focusClass, StatusDot, Switch } from "@/features/settings/settings-ui";
import { categoriesOf, groupByCategory, matchesEntry, signInLabel, signInOf, tokenInstead } from "@/lib/plugin-catalog";
import {
  changePlugin, connectPlugin, MANY_TOOLS, oauthMessage, refreshPlugin, removePlugin, serverHost, startPluginOAuth, toolSummary,
  type PluginCatalogEntry, type PluginConnection, type PluginOAuthInput, type PluginsOverview,
} from "@/lib/plugins-api";
import { routes } from "@/lib/routes";
import { ApiError, message } from "@/lib/task-api";

const linkClass = `rounded underline underline-offset-4 ${focusClass}`;
const NO_KEY = "Saving a token needs an encryption key on the server. Ask the operator to set PLUGIN_ENCRYPTION_KEY.";
const footerButton = `inline-flex min-h-10 items-center gap-1.5 rounded-md px-2.5 text-[0.8125rem] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none sm:min-h-8 ${focusClass}`;

/**
 * Sends the browser to sign in. The server looks the plugin up and registers this app with it, and answers with the address of the
 * service's own sign-in page; the sign-in is finished by the server's callback, which returns here (as `?oauth=`).
 */
async function signIn(input: PluginOAuthInput): Promise<void> {
  const { authorization_url: address } = await startPluginOAuth(input);
  window.location.assign(address);
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
  return <div className="space-y-1.5"><label htmlFor={id} className="text-sm font-medium">{label}</label>{children}{hint ? <p id={`${id}-hint`} className="text-xs leading-5 text-muted-foreground">{hint}</p> : null}</div>;
}

/** A read-only value with a copy button (the return address to register for an OAuth app). */
function CopyField({ id, value, label }: { id: string; value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return <div className="flex items-center gap-2">
    <input id={id} readOnly value={value} aria-label={label} onFocus={(event) => event.currentTarget.select()} className={`${inputClass} font-mono text-xs`} />
    <Button type="button" variant="outline" size="icon" aria-label={copied ? "Copied" : `Copy ${label.toLowerCase()}`}
      onClick={() => { void navigator.clipboard?.writeText(value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1800); }, () => {}); }}>{copied ? <Check /> : <Copy />}</Button>
  </div>;
}

/** Connects a catalog plugin or a server of the person's own, in a dialog: by signing in where the server offers it, else with a token. */
function ConnectDialog({ entry, overview, onClose }: { entry: PluginCatalogEntry | null; overview: PluginsOverview; onClose: () => void }) {
  const id = useId();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [header, setHeader] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [useToken, setUseToken] = useState(false);
  const [ownApp, setOwnApp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const credentials = overview.credentials;
  const kind = entry ? signInOf(entry) : "custom";
  const oauthFirst = entry ? entry.oauth && !useToken : false;
  const takesToken = entry ? (entry.auth !== "none" && (!entry.oauth || useToken)) : true;
  const needsToken = entry ? entry.auth === "required" || (entry.oauth && useToken) : false;
  const tokenBlocked = takesToken && !credentials;
  const signInOff = oauthFirst && !overview.oauth;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      if (oauthFirst && entry) { await signIn({ plugin_key: entry.key }); return; }
      if (entry) {
        pluginsStore.apply(await connectPlugin({ plugin_key: entry.key, ...(token.trim() ? { token: token.trim() } : {}) }));
        onClose();
        return;
      }
      const own = { plugin_key: "custom", name: name.trim(), server_url: url.trim() };
      if (ownApp && clientId.trim()) { await signIn({ ...own, client_id: clientId.trim(), ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}) }); return; }
      try {
        pluginsStore.apply(await connectPlugin({ ...own, ...(token.trim() ? { token: token.trim() } : {}), ...(header.trim() ? { header_name: header.trim() } : {}) }));
        onClose();
      } catch (failure) {
        // A server that asks for a sign-in is signed in to; one that registers no apps needs an OAuth app of the person's own.
        if (!(failure instanceof ApiError) || failure.code !== "plugin_oauth_required") throw failure;
        try { await signIn(own); }
        catch (problem) {
          if (problem instanceof ApiError && problem.code === "plugin_oauth_client_required") setOwnApp(true);
          throw problem;
        }
      }
    } catch (failure) { setError(message(failure)); setBusy(false); }
  }
  const title = entry ? `Connect ${entry.name}` : "Add your own server";
  const action = oauthFirst ? (busy ? "Opening sign-in…" : `Continue to ${entry?.name}`) : busy ? "Connecting…" : "Connect";
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent>
      <form onSubmit={submit} className="flex min-h-0 flex-col" aria-label={title} aria-busy={busy}>
        <div className="flex items-start gap-3.5 px-5 pb-3 pt-5">
          <PluginLogo pluginKey={entry?.key ?? "custom"} name={entry?.name ?? "Your own server"} />
          <div className="min-w-0 space-y-0.5"><DialogTitle>{title}</DialogTitle>
            <DialogDescription>{entry ? entry.description : "Connect any remote MCP server by its address."}</DialogDescription></div>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-3">
          {entry ? <p className="text-[0.8125rem] text-muted-foreground">{signInLabel(entry)}</p> : null}
          {oauthFirst && entry ? <p className="flex items-start gap-2.5 rounded-xl bg-muted/50 px-3.5 py-3 text-[0.8125rem] leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-4 shrink-0" aria-hidden="true" /><span>You will go to {entry.name} to sign in and choose what to allow. AgentForge never sees your password, and you can disconnect at any time.</span></p> : null}
          {signInOff ? <p role="status" className="text-sm leading-6 text-destructive">Signing in is not available on this server: it needs an encryption key to keep the access it is given. Ask the operator to set PLUGIN_ENCRYPTION_KEY.</p> : null}
          {!entry ? <>
            <Field id={`${id}-name`} label="Name"><input id={`${id}-name`} className={inputClass} value={name} onChange={(event) => setName(event.target.value)} required maxLength={60} placeholder="My tools" disabled={busy} autoComplete="off" /></Field>
            <Field id={`${id}-url`} label="Server address" hint="The address of a remote MCP server (Streamable HTTP), for example https://example.com/mcp. If it asks you to sign in, you will be sent to do that."><input id={`${id}-url`} aria-describedby={`${id}-url-hint`} className={inputClass} type="url" value={url} onChange={(event) => setUrl(event.target.value)} required maxLength={2048} placeholder="https://example.com/mcp" disabled={busy} autoComplete="off" autoCapitalize="none" spellCheck={false} /></Field>
          </> : null}
          {takesToken ? <Field id={`${id}-token`} label={`${entry?.token_label ?? "Access token"}${needsToken ? "" : " (optional)"}`}
            hint={tokenBlocked ? NO_KEY : entry?.token_help || "Sent to the server with every request. Stored encrypted and never shown again."}>
            <input id={`${id}-token`} aria-describedby={`${id}-token-hint`} className={inputClass} type="password" value={token} onChange={(event) => setToken(event.target.value)} required={needsToken} maxLength={4096} disabled={busy || tokenBlocked || ownApp} autoComplete="off" autoCapitalize="none" spellCheck={false} />
          </Field> : null}
          {kind === "oauth-or-token" ? <button type="button" className={`text-[0.8125rem] text-muted-foreground ${linkClass}`} onClick={() => { setUseToken((value) => !value); setError(""); }}>{useToken ? "Sign in instead" : entry ? tokenInstead(entry) : ""}</button> : null}
          {!entry ? <details className="text-sm" open={ownApp}><summary className={`cursor-pointer rounded text-muted-foreground ${focusClass}`}>Advanced</summary>
            <div className="space-y-4 pt-3">
              <Field id={`${id}-header`} label="Header name" hint="Where a token goes. Leave empty to send it as “Authorization: Bearer …”."><input id={`${id}-header`} aria-describedby={`${id}-header-hint`} className={inputClass} value={header} onChange={(event) => setHeader(event.target.value)} maxLength={64} placeholder="Authorization" disabled={busy} autoComplete="off" autoCapitalize="none" spellCheck={false} /></Field>
              <div className="space-y-3 rounded-xl border p-3.5">
                <label className="flex cursor-pointer items-start gap-2.5 text-sm"><input type="checkbox" className="mt-1 size-4 accent-primary" checked={ownApp} onChange={(event) => setOwnApp(event.target.checked)} disabled={busy} /><span><span className="block font-medium">Sign in with my own OAuth app</span><span className="block text-xs leading-5 text-muted-foreground">For a server that does not register apps by itself. Create an OAuth app with the service and use this return address.</span></span></label>
                {ownApp ? <>
                  <Field id={`${id}-redirect`} label="Return address (redirect URI)"><CopyField id={`${id}-redirect`} value={overview.oauth_redirect_uri} label="Return address" /></Field>
                  <Field id={`${id}-client`} label="Client ID"><input id={`${id}-client`} className={inputClass} value={clientId} onChange={(event) => setClientId(event.target.value)} required maxLength={1024} disabled={busy} autoComplete="off" autoCapitalize="none" spellCheck={false} /></Field>
                  <Field id={`${id}-secret`} label="Client secret (optional)" hint="Only if the app has one. Stored encrypted and never shown again."><input id={`${id}-secret`} aria-describedby={`${id}-secret-hint`} className={inputClass} type="password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} maxLength={4096} disabled={busy || !credentials} autoComplete="off" autoCapitalize="none" spellCheck={false} /></Field>
                </> : null}
              </div>
            </div>
          </details> : null}
          <p className="text-xs leading-5 text-muted-foreground">{entry ? "" : "Only connect servers you trust: the agent sends them what each tool call needs. "}Nothing is saved unless the server answers and lists its tools.</p>
          {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
        </div>
        <div className="flex flex-wrap justify-end gap-2 px-5 pb-5 pt-3">
          <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" className="min-h-10 sm:min-h-8" disabled={busy || signInOff || (needsToken && tokenBlocked)}>{action}</Button>
        </div>
      </form>
    </DialogContent>
  </Dialog>;
}

/** One connected plugin: its tools (each can be switched off), a fresh read of the server, its token or sign-in, and removal. */
function ManageDialog({ connection, entry, credentials, onClose }: { connection: PluginConnection; entry: PluginCatalogEntry | undefined; credentials: boolean; onClose: () => void }) {
  const id = useId();
  const [busy, setBusy] = useState<"" | "save" | "refresh" | "remove" | "signin">("");
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [token, setToken] = useState("");
  const signedIn = connection.auth === "oauth";
  const canHoldToken = !signedIn && (!entry || entry.auth !== "none");
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
  async function reconnect() {
    setBusy("signin"); setError("");
    try { await signIn({ plugin_key: connection.plugin_key, connection_id: connection.id }); }
    catch (failure) { setError(message(failure)); setBusy(""); }
  }
  const failing = connection.status !== "connected";
  // A refresh or a save finishes on its own (the shared list is updated either way), so the dialog may be closed meanwhile; only a
  // removal in flight keeps it open, to show how it ended.
  return <Dialog open onOpenChange={(open) => { if (!open && busy !== "remove" && busy !== "signin") onClose(); }}>
    <DialogContent className="sm:w-[min(38rem,calc(100vw-2rem))]">
      <div className="flex items-start gap-3.5 px-5 pb-3 pt-5">
        <PluginLogo pluginKey={connection.plugin_key} name={connection.name} />
        <div className="min-w-0 space-y-0.5"><DialogTitle className="break-words">{connection.name}</DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5"><McpMark className="size-3.5" />{serverHost(connection.server_url)} · {toolSummary(connection)}{signedIn ? " · signed in" : connection.has_credential ? " · token saved" : ""}</DialogDescription></div>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-3">
        {failing ? <div role="status" className="flex flex-wrap items-start gap-x-3 gap-y-2.5 rounded-xl bg-destructive/10 px-3.5 py-3 text-[0.8125rem] leading-5 text-destructive">
          <p className="flex min-w-0 flex-1 basis-56 items-start gap-2"><CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" /><span>{connection.last_error ?? "The server could not be reached."}{signedIn && connection.status === "error" ? " Refresh to try again, or reconnect." : !signedIn ? ` Refresh to try again${canHoldToken ? ", or replace the token" : ""}.` : ""}</span></p>
          {signedIn ? <Button type="button" size="sm" className="min-h-10 shrink-0 sm:min-h-7" disabled={busy !== ""} onClick={() => void reconnect()}>{busy === "signin" ? "Opening sign-in…" : "Reconnect"}</Button> : null}
        </div> : null}
        <div className="space-y-2.5">
          <h4 className="text-sm font-medium">Tools<span className="ml-2 text-sm font-normal tabular-nums text-muted-foreground">{connection.enabled_tool_count}/{connection.tool_count}</span></h4>
          {connection.tool_count > MANY_TOOLS ? <p className="text-xs leading-5 text-muted-foreground">This plugin offers {connection.tool_count} tools. Each one the agent can use adds to every request, so switch off the ones you do not need.</p> : null}
          {connection.tools.length ? <ul className="divide-y overflow-hidden rounded-xl border">{connection.tools.map((tool) => <li key={tool.name} className="flex items-start gap-3 px-3 py-2.5">
            <input type="checkbox" className="mt-1 size-4 shrink-0 accent-primary" checked={tool.enabled} disabled={busy !== ""} onChange={(event) => {
              const off = new Set(connection.tools.filter((item) => !item.enabled).map((item) => item.name));
              if (event.target.checked) off.delete(tool.name); else off.add(tool.name);
              void change({ disabled_tools: [...off] });
            }} aria-label={`Allow ${tool.title || tool.name}`} id={`tool-${connection.id}-${tool.name}`} />
            <label htmlFor={`tool-${connection.id}-${tool.name}`} className="min-w-0 flex-1 cursor-pointer space-y-0.5">
              <span className="flex flex-wrap items-center gap-x-2 text-sm"><span className="break-all font-medium">{tool.title || tool.name}</span>
                <span className={`text-xs ${tool.read_only ? "text-muted-foreground" : "text-amber-600 dark:text-amber-400"}`}>{tool.read_only ? "Only reads" : "May change things"}</span></span>
              {tool.description ? <span className="line-clamp-2 block text-xs leading-5 text-muted-foreground">{tool.description}</span> : null}
            </label>
          </li>)}</ul> : <p className="text-sm text-muted-foreground">This server lists no tools.</p>}
        </div>
        {replacing ? <form onSubmit={replaceToken} className="flex flex-wrap items-end gap-2" aria-label={`New token for ${connection.name}`}>
          <div className="min-w-0 flex-1 basis-56 space-y-1.5"><label htmlFor={`${id}-token`} className="text-sm font-medium">{entry?.token_label ?? "Access token"}</label>
            <input id={`${id}-token`} className={inputClass} type="password" value={token} onChange={(event) => setToken(event.target.value)} required maxLength={4096} autoComplete="off" autoCapitalize="none" spellCheck={false} disabled={busy !== "" || !credentials} aria-describedby={credentials ? undefined : `${id}-nokey`} /></div>
          <Button type="submit" className="min-h-10 sm:min-h-8" disabled={busy !== "" || !credentials || !token.trim()}>{busy === "save" ? "Checking…" : "Save token"}</Button>
          {!credentials ? <p id={`${id}-nokey`} className="basis-full text-xs leading-5 text-muted-foreground">{NO_KEY}</p> : null}
        </form> : null}
        {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
      </div>
      <div className="flex flex-wrap items-center gap-x-0.5 gap-y-1 border-t px-3 py-2.5 sm:px-4">
        {confirming ? <span className="mr-auto flex items-center gap-1 text-sm"><span className="hidden text-muted-foreground sm:inline">Remove {connection.name}?</span>
          <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" onClick={() => setConfirming(false)} disabled={busy !== ""}>Keep</Button>
          <Button type="button" variant="destructive" className="min-h-10 sm:min-h-8" onClick={() => void remove()} disabled={busy !== ""}>{busy === "remove" ? "Removing…" : "Remove"}</Button></span>
          : <button type="button" className={`${footerButton} mr-auto hover:text-destructive`} disabled={busy !== ""} onClick={() => setConfirming(true)}>Remove</button>}
        <button type="button" className={footerButton} disabled={busy !== ""} onClick={() => void refresh()}><RefreshCw className={`size-3.5 ${busy === "refresh" ? "motion-safe:animate-spin" : ""}`} aria-hidden="true" />{busy === "refresh" ? "Refreshing…" : "Refresh"}</button>
        {signedIn && !failing ? <button type="button" className={footerButton} disabled={busy !== ""} onClick={() => void reconnect()}><LockKeyhole className="size-3.5" aria-hidden="true" />{busy === "signin" ? "Opening sign-in…" : "Reconnect"}</button> : null}
        {canHoldToken ? <button type="button" className={footerButton} disabled={busy !== ""} aria-expanded={replacing} onClick={() => setReplacing((value) => !value)}><KeyRound className="size-3.5" aria-hidden="true" />{connection.has_credential ? "Replace token" : "Add token"}</button> : null}
        {entry?.docs_url ? <a href={entry.docs_url} target="_blank" rel="noopener noreferrer" className={footerButton}><ExternalLink className="size-3.5" aria-hidden="true" />About<span className="sr-only"> {connection.name} (opens in a new tab)</span></a> : null}
        <Button type="button" className="ml-1 min-h-10 sm:min-h-8" onClick={onClose} disabled={busy === "remove" || busy === "signin"}>Done</Button>
      </div>
    </DialogContent>
  </Dialog>;
}

type Open = { kind: "connect"; key: string | null } | { kind: "manage"; id: string };

/** The pills that filter the catalog by category. */
function CategoryPills({ categories, value, onChange }: { categories: string[]; value: string | null; onChange: (category: string | null) => void }) {
  const pill = (active: boolean) => `inline-flex h-8 shrink-0 items-center rounded-full px-3.5 text-[13px] transition-colors motion-reduce:transition-none ${focusClass} ${active ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground"}`;
  return <div role="group" aria-label="Filter by category" className="-mx-1 flex gap-1 overflow-x-auto px-1 py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
    <button type="button" aria-pressed={value === null} className={pill(value === null)} onClick={() => onChange(null)}>All</button>
    {categories.map((category) => <button key={category} type="button" aria-pressed={value === category} className={pill(value === category)} onClick={() => onChange(value === category ? null : category)}>{category}</button>)}
  </div>;
}

function Banner({ result, onDismiss }: { result: OAuthResult; onDismiss: () => void }) {
  const good = result.status === "connected";
  return <div role={good ? "status" : "alert"} className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-sm leading-6 ${good ? "bg-card" : "border-destructive/30 bg-destructive/5 text-destructive"}`}>
    {good ? <Check className="mt-1 size-4 shrink-0 text-emerald-500" aria-hidden="true" /> : <CircleAlert className="mt-1 size-4 shrink-0" aria-hidden="true" />}
    <p className="min-w-0 flex-1">{good ? <>Signed in. <strong className="font-medium">{result.plugin}</strong> is connected.</> : oauthMessage(result.code)}</p>
    <button type="button" aria-label="Dismiss" onClick={onDismiss} className={`-m-1 grid size-8 shrink-0 place-items-center rounded-md opacity-70 hover:opacity-100 ${focusClass}`}><X className="size-4" aria-hidden="true" /></button>
  </div>;
}

/** Connected plugins and the catalog to add more: shared by the Plugins page (with its `tabs` and the result of a sign-in) and Settings → Plugins. */
export function PluginsPanel({ tabs, result }: { tabs?: ReactNode; result?: OAuthResult | null }) {
  const router = useRouter();
  const { status, overview, error } = usePlugins();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [open, setOpen] = useState<Open | null>(null);
  const [notice, setNotice] = useState("");
  const [banner, setBanner] = useState<OAuthResult | null>(result ?? null);
  // Back from signing in: say how it went, read the list again (the server added the connection), and tidy the address.
  useEffect(() => {
    if (!result) return;
    pluginsStore.reload();
    router.replace(routes.plugins(), { scroll: false });
  }, [result, router]);
  const needle = query.trim();
  const enabled = !!overview?.enabled;
  const connected = overview?.connections ?? [];
  const connectedKeys = new Set(connected.map((connection) => connection.plugin_key));
  const available = (overview?.catalog ?? []).filter((entry) => !connectedKeys.has(entry.key));
  const full = !!overview && connected.length >= overview.max_connections;
  const shownConnected = connected.filter((connection) => !needle || [connection.name, serverHost(connection.server_url)].some((text) => text.toLowerCase().includes(needle.toLowerCase())));
  const filtered = available.filter((entry) => matchesEntry(entry, needle) && (category === null || entry.category === category));
  const groups = groupByCategory(filtered);
  const showCustom = category === null && (!needle || ["your own server", "custom", "mcp", "connect any remote mcp server by its address"].some((text) => text.includes(needle.toLowerCase())));
  const nothing = enabled && !!needle && !shownConnected.length && !filtered.length && !showCustom;
  const managed = open?.kind === "manage" ? connected.find((connection) => connection.id === open.id) : undefined;
  const connecting = open?.kind === "connect" ? (open.key ? available.find((entry) => entry.key === open.key) : null) : undefined;
  function reconnect(connection: PluginConnection) {
    setNotice("");
    void signIn({ plugin_key: connection.plugin_key, connection_id: connection.id }).catch((failure) => setNotice(message(failure)));
  }
  return <div className="space-y-9" aria-busy={status === "loading" && !overview}>
    {banner ? <Banner result={banner} onDismiss={() => setBanner(null)} /> : null}
    <Toolbar tabs={tabs}>
      <SearchField value={query} onChange={setQuery} label="Search plugins" />
      <RefreshButton label="Refresh plugins" busy={status === "loading"} onClick={() => pluginsStore.reload()} />
      <AddMenu disabled={!enabled}>{(close) => <>
        <MenuItem icon={<Plus className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />} hint="Pick one from the list below" onClick={() => { close(); document.getElementById("available-plugins")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>Browse plugins</MenuItem>
        <MenuItem disabled={full} icon={<PluginLogo pluginKey="custom" name="Your own server" size="xs" />} hint="Any remote MCP server" onClick={() => { close(); setOpen({ kind: "connect", key: null }); }}>Your own server</MenuItem>
        {full && overview ? <p className="px-2.5 py-2 text-xs leading-5 text-muted-foreground">You have connected the most plugins allowed ({overview.max_connections}). Remove one to add another.</p> : null}
      </>}</AddMenu>
    </Toolbar>
    {error && !overview ? <p role="alert" className="text-sm text-destructive">{error} <button type="button" className={linkClass} onClick={() => pluginsStore.reload()}>Try again</button></p> : null}
    {notice ? <p role="alert" className="text-sm text-destructive">{notice}</p> : null}
    {!overview && !error ? <RowSkeleton label="Loading plugins…" /> : null}
    {overview && !overview.enabled ? <p role="status" className="text-sm leading-6 text-muted-foreground">Plugins are turned off on this server. The operator can turn them on with PLUGINS_ENABLED.</p> : null}
    {enabled && overview ? <>
      {nothing ? <p role="status" className="text-sm text-muted-foreground">No plugins match “{needle}”.</p> : null}
      {!needle || shownConnected.length ? <ListSection title="Connected" count={connected.length ? connected.length : undefined}>
        {shownConnected.length ? <RowList label="Connected plugins">{shownConnected.map((connection) => {
          const attention = connection.status !== "connected";
          return <ListRow key={connection.id} rowProps={{ "data-plugin": connection.plugin_key }} leading={<PluginLogo pluginKey={connection.plugin_key} name={connection.name} />} title={connection.name}
            subtitle={connection.status === "reauthorize" ? <span className="inline-flex items-center gap-1.5 text-amber-600 dark:text-amber-400"><StatusDot tone="warn" />Sign in again to keep using it</span>
              : attention ? <span className="text-amber-600 dark:text-amber-400">Needs attention</span>
                : <span className="inline-flex items-center gap-1.5"><McpMark className="size-3 shrink-0" />{serverHost(connection.server_url)} · {toolSummary(connection)}</span>}
            onOpen={() => setOpen({ kind: "manage", id: connection.id })} openLabel={`Manage ${connection.name}`}
            trailing={connection.status === "reauthorize" && connection.auth === "oauth"
              ? <Button type="button" variant="outline" size="sm" className="min-h-9" aria-label={`Reconnect ${connection.name}`} onClick={() => reconnect(connection)}>Reconnect</Button>
              : <Switch checked={connection.enabled} label={`${connection.name}: use in new conversations`} onChange={(next) => {
                setNotice("");
                void changePlugin(connection.id, { enabled: next }).then((updated) => pluginsStore.apply(updated), (failure) => { setNotice(message(failure)); pluginsStore.reload(); });
              }} />} />;
        })}</RowList>
          : <EmptyLine icon={<McpMark className="size-4 shrink-0" />}>No plugins connected yet. Add one below and the agent can use its tools in new conversations.</EmptyLine>}
      </ListSection> : null}
      {filtered.length || showCustom || (!needle && category === null) ? <section id="available-plugins" className="scroll-mt-6 space-y-6" aria-label="Available plugins">
        <div className="space-y-3">
          <div className="space-y-1"><h3 className="flex items-baseline gap-2 text-base font-semibold tracking-tight">Available<span className="text-sm font-normal tabular-nums text-muted-foreground">{available.length}</span></h3>
            {full ? <p className="text-[0.8125rem] leading-5 text-muted-foreground">You have connected the most plugins allowed ({overview.max_connections}). Remove one to add another.</p> : null}</div>
          {categoriesOf(available).length > 1 ? <CategoryPills categories={categoriesOf(available)} value={category} onChange={setCategory} /> : null}
        </div>
        {groups.map((group) => <div key={group.category} className="space-y-1.5">
          {category === null ? <h4 className="text-[0.8125rem] font-medium text-muted-foreground">{group.category}</h4> : null}
          <RowList label={`${group.category} plugins`}>{group.entries.map((entry) => <ListRow key={entry.key} leading={<PluginLogo pluginKey={entry.key} name={entry.name} />} title={entry.name} subtitle={entry.description}
            onOpen={full ? undefined : () => setOpen({ kind: "connect", key: entry.key })} openLabel={`Connect ${entry.name}`}
            trailing={<PlusButton label={`Connect ${entry.name}`} disabled={full} icon={<Plus className="size-5" aria-hidden="true" />} onClick={() => setOpen({ kind: "connect", key: entry.key })} />} />)}</RowList>
        </div>)}
        {showCustom ? <div className="space-y-1.5">
          {category === null && groups.length ? <h4 className="text-[0.8125rem] font-medium text-muted-foreground">Your own</h4> : null}
          <RowList label="Your own server"><ListRow leading={<PluginLogo pluginKey="custom" name="Your own server" />} title="Your own server" subtitle="Connect any remote MCP server by its address"
            onOpen={full ? undefined : () => setOpen({ kind: "connect", key: null })} openLabel="Add your own server"
            trailing={<PlusButton label="Add your own server" disabled={full} icon={<Plus className="size-5" aria-hidden="true" />} onClick={() => setOpen({ kind: "connect", key: null })} />} /></RowList>
        </div> : null}
      </section> : null}
      <p className="max-w-prose text-xs leading-5 text-muted-foreground">Plugins are other people’s services: the agent sends them what a tool call needs, and what they return is treated as data, not instructions. In Ask me mode, a tool that may change something waits for your approval. New conversations start with the plugins switched on here.</p>
    </> : null}
    {managed && overview ? <ManageDialog key={managed.id} connection={managed} entry={overview.catalog.find((item) => item.key === managed.plugin_key)} credentials={overview.credentials} onClose={() => setOpen(null)} /> : null}
    {overview && open?.kind === "connect" && connecting !== undefined ? <ConnectDialog key={open.key ?? "custom"} entry={connecting} overview={overview} onClose={() => setOpen(null)} /> : null}
  </div>;
}
