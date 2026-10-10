"use client";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { api, message } from "@/lib/task-api";
import { starterDefinition, type AgentDefinition, type CustomAgent } from "@/lib/agents-api";

export function AgentEditor({ projectId, existing, onSaved }: { projectId: string; existing: CustomAgent | null; onSaved: (agent: CustomAgent) => void }) {
  const initial = existing ? { ...existing.definition, version: existing.current_version + 1, allowed_tools: [...new Set(["read_task_input", "read_research_source", ...existing.definition.allowed_tools])] } : starterDefinition();
  const [definition, setDefinition] = useState<AgentDefinition>(initial);
  const [json, setJson] = useState(JSON.stringify(initial, null, 2));
  const [jsonMode, setJsonMode] = useState(false);
  const [hosts, setHosts] = useState((initial.permissions.allowed_web_hosts ?? []).join(", "));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  function change(patch: Partial<AgentDefinition>) { setDefinition((current) => ({ ...current, ...patch })); }
  function toggleJson() {
    if (!jsonMode) { setJson(JSON.stringify({ ...definition, permissions: { ...definition.permissions, allowed_web_hosts: hosts.split(/[\s,]+/).filter(Boolean) } }, null, 2)); setJsonMode(true); }
    else {
      try {
        const parsed = JSON.parse(json) as AgentDefinition;
        if (!parsed?.name || !parsed.permissions || !parsed.budget || !Array.isArray(parsed.allowed_tools)) throw new Error("Keep the name, permissions, budget and allowed_tools fields in the definition.");
        setDefinition(parsed); setHosts((parsed.permissions.allowed_web_hosts ?? []).join(", ")); setJsonMode(false); setError("");
      } catch (error) { setError(message(error)); }
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      const payload: AgentDefinition = jsonMode ? JSON.parse(json) as AgentDefinition : { ...definition, permissions: { ...definition.permissions, allowed_web_hosts: hosts.split(/[\s,]+/).map((host) => host.toLowerCase()).filter(Boolean) } };
      if (payload.version !== (existing ? existing.current_version + 1 : 1)) throw new Error(`Use version ${existing ? existing.current_version + 1 : 1} for this definition.`);
      const result = await api<CustomAgent>("/agents", { method: "POST", body: JSON.stringify({ project_id: projectId, definition: payload }) });
      onSaved(result);
    } catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  return <form onSubmit={save} className="space-y-5" aria-busy={busy}>
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">{existing ? `New version of ${existing.name}` : "Create a research agent"}</h2><Button type="button" variant="outline" size="sm" onClick={toggleJson} disabled={busy}>{jsonMode ? "Edit as form" : "Edit JSON"}</Button></div>
    <p className="text-sm leading-6 text-muted-foreground">Custom agents use the shared research runtime. Each saved version is immutable, with scoped web tools and a bounded budget.</p>
    {jsonMode ? <div className="space-y-2"><label htmlFor="agent-json" className="text-sm font-medium">Agent definition JSON</label><textarea id="agent-json" value={json} onChange={(event) => setJson(event.target.value)} rows={24} spellCheck={false} className={`${inputClass} font-mono text-xs leading-6`} maxLength={20000} disabled={busy} required /></div> : <fieldset disabled={busy} className="space-y-5"><legend className="sr-only">Agent definition</legend>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_7rem]"><div className="space-y-2"><label htmlFor="agent-name" className="text-sm font-medium">Name</label><input id="agent-name" className={inputClass} value={definition.name} onChange={(event) => change({ name: event.target.value })} required minLength={3} maxLength={64} pattern="[a-z][a-z0-9-]+" readOnly={!!existing} /><p className="text-xs text-muted-foreground">Lowercase letters, numbers and hyphens.</p></div><div className="space-y-2"><label htmlFor="agent-version" className="text-sm font-medium">Version</label><input id="agent-version" value={definition.version} readOnly className={`${inputClass} bg-muted/30 tabular-nums`} /></div></div>
      <div className="space-y-2"><label htmlFor="agent-description" className="text-sm font-medium">Description</label><input id="agent-description" value={definition.description} onChange={(event) => change({ description: event.target.value })} className={inputClass} minLength={10} maxLength={400} required /></div>
      <div className="space-y-2"><label htmlFor="agent-prompt" className="text-sm font-medium">Instructions</label><textarea id="agent-prompt" className={`${inputClass} leading-6`} rows={7} required minLength={30} maxLength={10000} value={definition.system_prompt} onChange={(event) => change({ system_prompt: event.target.value })} /><p className="text-xs leading-5 text-muted-foreground">Describe the research method and evidence standards. Instructions cannot grant tools or permissions beyond the task.</p></div>
      <fieldset className="space-y-3"><legend className="mb-2 text-sm font-medium">Allowed tools</legend>{[["read_task_input", "Read full task inputs and attachments"], ["read_research_source", "Read stored source evidence"], ["web_search", "Search the web"], ["fetch_webpage", "Fetch source pages"]].map(([key, label]) => <label key={key} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={definition.allowed_tools.includes(key)} onChange={(event) => change({ allowed_tools: event.target.checked ? [...definition.allowed_tools, key] : definition.allowed_tools.filter((tool) => tool !== key) })} className="size-4 accent-primary" />{label}</label>)}</fieldset>
      <div className="space-y-2"><label htmlFor="agent-domains" className="text-sm font-medium">Allowed source domains</label><input id="agent-domains" className={inputClass} value={hosts} onChange={(event) => setHosts(event.target.value)} placeholder="example.org, docs.example.com" /><p className="text-xs leading-5 text-muted-foreground">Enter exact public domains to restrict source access. Leave empty to inherit the task’s permitted sources.</p></div>
      <fieldset className="grid gap-4 sm:grid-cols-2"><legend className="mb-3 text-sm font-medium">Agent budget</legend>{([['max_steps', 'Maximum model steps', 1, 12, 1], ['max_tool_calls', 'Maximum tool calls', 1, 20, 1], ['max_llm_cost_usd', 'Maximum model cost (USD)', 0.01, 1, 0.01], ['max_wall_time_sec', 'Maximum duration (seconds)', 10, 300, 1]] as const).map(([key, label, min, max, step]) => <div key={key} className="space-y-2"><label htmlFor={`agent-${key}`} className="text-xs font-medium">{label}</label><input id={`agent-${key}`} type="number" min={min} max={max} step={step} required value={definition.budget[key]} onChange={(event) => change({ budget: { ...definition.budget, [key]: Number(event.target.value) } })} className={`${inputClass} tabular-nums`} /></div>)}</fieldset>
    </fieldset>}
    {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
    <Button type="submit" disabled={busy || !projectId}>{busy ? "Saving…" : existing ? `Save immutable version ${existing.current_version + 1}` : "Create agent"}</Button>
  </form>;
}
