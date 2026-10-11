"use client";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { editableTools, toolLabels } from "@/features/agents/agent-tools";
import { cardClass, Group } from "@/features/settings/settings-ui";
import { api, message } from "@/lib/task-api";
import { starterDefinition, type AgentDefinition, type CustomAgent } from "@/lib/agents-api";

const labelClass = "text-sm font-medium";
const hintClass = "text-[0.8125rem] leading-5 text-muted-foreground";
const fieldClass = `${inputClass} h-10`;

export function AgentEditor({ projectId, existing, onSaved, onCancel }: { projectId?: string; existing: CustomAgent | null; onSaved: (agent: CustomAgent) => void; onCancel?: () => void }) {
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
        if (!parsed?.name || !parsed.permissions || !Array.isArray(parsed.allowed_tools)) throw new Error("Keep the name, permissions and allowed_tools fields in the definition.");
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
  return <form onSubmit={save} className="space-y-6" aria-busy={busy}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1.5"><h3 className="text-xl font-semibold tracking-tight">{existing ? `New version of ${existing.name}` : "Create a research agent"}</h3><p className="max-w-prose text-sm leading-6 text-muted-foreground">Custom agents run on the same agent loop as the assistant. Each saved version is immutable, with scoped web tools.</p></div>
      <Button type="button" variant="outline" size="sm" onClick={toggleJson} disabled={busy}>{jsonMode ? "Edit as form" : "Edit JSON"}</Button>
    </div>
    {jsonMode ? <div className="space-y-2"><label htmlFor="agent-json" className={labelClass}>Agent definition JSON</label><textarea id="agent-json" value={json} onChange={(event) => setJson(event.target.value)} rows={24} spellCheck={false} className={`${inputClass} font-mono text-xs leading-6`} maxLength={20000} disabled={busy} required /></div> : <fieldset disabled={busy} className="min-w-0 space-y-6"><legend className="sr-only">Agent definition</legend>
      <Group title="Identity">
        <div className={`${cardClass} space-y-5 p-4 sm:p-5`}>
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_7rem]">
            <div className="space-y-2"><label htmlFor="agent-name" className={labelClass}>Name</label><input id="agent-name" className={fieldClass} value={definition.name} onChange={(event) => change({ name: event.target.value })} required minLength={3} maxLength={64} pattern="[a-z][a-z0-9\-]+" readOnly={!!existing} aria-describedby="agent-name-hint" /><p id="agent-name-hint" className={hintClass}>Lowercase letters, numbers and hyphens.</p></div>
            <div className="space-y-2"><label htmlFor="agent-version" className={labelClass}>Version</label><input id="agent-version" value={definition.version} readOnly className={`${fieldClass} bg-muted/40 tabular-nums`} /></div>
          </div>
          <div className="space-y-2"><label htmlFor="agent-description" className={labelClass}>Description</label><input id="agent-description" value={definition.description} onChange={(event) => change({ description: event.target.value })} className={fieldClass} minLength={10} maxLength={400} required /></div>
        </div>
      </Group>
      <Group title="Instructions">
        <div className={`${cardClass} space-y-2 p-4 sm:p-5`}>
          <label htmlFor="agent-prompt" className="sr-only">Instructions</label>
          <textarea id="agent-prompt" className={`${inputClass} leading-6`} rows={8} required minLength={30} maxLength={10000} value={definition.system_prompt} onChange={(event) => change({ system_prompt: event.target.value })} aria-describedby="agent-prompt-hint" />
          <p id="agent-prompt-hint" className={hintClass}>Describe how it should work and what a good answer looks like. Instructions cannot grant tools or permissions beyond the task.</p>
        </div>
      </Group>
      <fieldset className="space-y-3"><legend className="mb-3 text-sm font-medium">Allowed tools</legend>
        <div className={`${cardClass} divide-y`}>{editableTools.map(({ key, hint }) => <label key={key} className="flex cursor-pointer items-start gap-3 px-4 py-3.5 transition-colors hover:bg-muted/40 has-[:focus-visible]:bg-muted/40 has-[:disabled]:cursor-not-allowed motion-reduce:transition-none sm:px-5">
          <input type="checkbox" checked={definition.allowed_tools.includes(key)} onChange={(event) => change({ allowed_tools: event.target.checked ? [...definition.allowed_tools, key] : definition.allowed_tools.filter((tool) => tool !== key) })} className="mt-0.5 size-4 shrink-0 accent-primary" aria-describedby={`tool-${key}-hint`} />
          <span className="min-w-0 space-y-0.5"><span className="block text-sm font-medium">{toolLabels[key]}</span><span id={`tool-${key}-hint`} className={`block ${hintClass}`}>{hint}</span></span>
        </label>)}</div>
      </fieldset>
      <Group title="Sources">
        <div className={`${cardClass} space-y-2 p-4 sm:p-5`}>
          <label htmlFor="agent-domains" className={labelClass}>Allowed source domains</label>
          <input id="agent-domains" className={fieldClass} value={hosts} onChange={(event) => setHosts(event.target.value)} placeholder="example.org, docs.example.com" aria-describedby="agent-domains-hint" />
          <p id="agent-domains-hint" className={hintClass}>Enter exact public domains to restrict source access. Leave empty to inherit the task’s permitted sources.</p>
        </div>
      </Group>
    </fieldset>}
    {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
    <div className="flex flex-wrap items-center gap-2 border-t pt-5">
      <Button type="submit" size="lg" disabled={busy}>{busy ? "Saving…" : existing ? `Save version ${existing.current_version + 1}` : "Create agent"}</Button>
      {onCancel ? <Button type="button" variant="ghost" size="lg" disabled={busy} onClick={onCancel}>Cancel</Button> : null}
    </div>
  </form>;
}
