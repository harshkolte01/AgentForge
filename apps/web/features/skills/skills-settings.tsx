"use client";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ChevronDown, Download, Pencil, Plus, RefreshCw, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { skillsStore, useSkills } from "@/features/skills/use-skills";
import { focusClass, sectionClass, SectionHeader, subheadingClass, subsectionClass } from "@/features/settings/settings-ui";
import {
  changeSkill, copyName, createSkill, DESCRIPTION_MAX, fetchSkill, formatSkillMarkdown, INSTRUCTIONS_MAX, nameProblem, parseSkillMarkdown,
  removeSkill, type Skill, type SkillFields,
} from "@/lib/skills-api";
import { message } from "@/lib/task-api";

const linkClass = `rounded underline underline-offset-4 ${focusClass}`;
const actionClass = `inline-flex min-h-10 items-center gap-1.5 rounded-md px-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50 sm:min-h-8 ${focusClass}`;
const MAX_IMPORT_BYTES = 100_000;

function Switch({ checked, label, disabled, onChange }: { checked: boolean; label: string; disabled?: boolean; onChange: (next: boolean) => void }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} title={label} disabled={disabled} onClick={() => onChange(!checked)}
    className={`relative inline-flex h-6 w-10 shrink-0 items-center rounded-full border transition-colors disabled:opacity-50 motion-reduce:transition-none ${focusClass} ${checked ? "border-transparent bg-primary" : "bg-muted"}`}>
    <span aria-hidden="true" className={`inline-block size-4 rounded-full bg-background shadow transition-transform motion-reduce:transition-none ${checked ? "translate-x-5" : "translate-x-1"}`} />
  </button>;
}

/** Writes or edits one of the person's own skills. `existing` is the skill being edited (with its instructions); none means a new one. */
function SkillForm({ existing, initial, onClose }: { existing: Skill | null; initial: Partial<SkillFields>; onClose: () => void }) {
  const id = useId();
  const [name, setName] = useState(initial.name ?? "");
  const [description, setDescription] = useState(initial.description ?? "");
  const [instructions, setInstructions] = useState(initial.instructions ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const problem = name.trim() ? nameProblem(name) : null;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    const fields = { name: name.trim(), description: description.trim(), instructions };
    try {
      const saved = existing ? await changeSkill(existing.key, fields) : await createSkill(fields);
      skillsStore.apply(saved);
      onClose();
    } catch (failure) {
      setError(message(failure));
      setBusy(false);
    }
  }
  return <form onSubmit={submit} className="space-y-4 rounded-xl border bg-muted/30 p-4" aria-label={existing ? `Edit ${existing.name}` : "New skill"} aria-busy={busy}>
    <div className="space-y-1.5">
      <label htmlFor={`${id}-name`} className="text-sm font-medium">Name</label>
      <div className="flex items-center gap-2"><span aria-hidden="true" className="font-mono text-sm text-muted-foreground">/</span>
        <input id={`${id}-name`} aria-describedby={`${id}-name-hint`} aria-invalid={!!problem} className={`${inputClass} font-mono`} value={name} onChange={(event) => setName(event.target.value)} required maxLength={40} placeholder="review-contract" disabled={busy} autoComplete="off" autoCapitalize="none" spellCheck={false} /></div>
      <p id={`${id}-name-hint`} className={`text-xs leading-5 ${problem ? "text-destructive" : "text-muted-foreground"}`}>{problem ?? "What you type after “/” to ask for it, and what the agent loads it by."}</p>
    </div>
    <div className="space-y-1.5">
      <label htmlFor={`${id}-description`} className="text-sm font-medium">Description</label>
      <textarea id={`${id}-description`} aria-describedby={`${id}-description-hint`} className={`${inputClass} min-h-16 resize-y`} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} required maxLength={DESCRIPTION_MAX} placeholder="Review a contract for risky clauses. Use when asked to check, review or summarise a contract." disabled={busy} />
      <p id={`${id}-description-hint`} className="flex justify-between gap-3 text-xs leading-5 text-muted-foreground"><span>Say what it does and when to use it. It is all the agent sees before deciding to load the skill.</span><span className="shrink-0 tabular-nums">{description.length}/{DESCRIPTION_MAX}</span></p>
    </div>
    <div className="space-y-1.5">
      <label htmlFor={`${id}-instructions`} className="text-sm font-medium">Instructions</label>
      <textarea id={`${id}-instructions`} aria-describedby={`${id}-instructions-hint`} className={`${inputClass} min-h-56 resize-y font-mono text-[13px] leading-5`} rows={12} value={instructions} onChange={(event) => setInstructions(event.target.value)} required placeholder={"# Review a contract\n\n1. Read the whole contract.\n2. List clauses that create risk…"} disabled={busy} spellCheck />
      <p id={`${id}-instructions-hint`} className="flex justify-between gap-3 text-xs leading-5 text-muted-foreground"><span>Steps for the agent to follow, in Markdown. They apply within the platform rules and cannot give it new permissions.</span><span className={`shrink-0 tabular-nums ${instructions.length > INSTRUCTIONS_MAX ? "text-destructive" : ""}`}>{instructions.length.toLocaleString("en-US")}/{INSTRUCTIONS_MAX.toLocaleString("en-US")}</span></p>
    </div>
    {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
    <div className="flex flex-wrap justify-end gap-2">
      <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" onClick={onClose} disabled={busy}>Cancel</Button>
      <Button type="submit" className="min-h-10 sm:min-h-8" disabled={busy || !!problem}>{busy ? "Saving…" : existing ? "Save changes" : "Add skill"}</Button>
    </div>
  </form>;
}

/** One skill in a list: its name and description, a switch, and what can be done with it. */
function SkillRow({ skill, onEdit, onCopy }: { skill: Skill; onEdit?: (full: Skill) => void; onCopy?: (full: Skill) => void }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<{ detail: Skill; at: string | null } | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "load" | "remove">("");
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const custom = skill.source === "custom";
  // The text read earlier belongs to the version it was read at: a saved change (its `updated_at` moves) makes it stale, and it is read
  // again if it is on show.
  const text = full && full.at === skill.updated_at ? full.detail : null;
  useEffect(() => {
    if (!open || text) return;
    let current = true;
    void fetchSkill(skill.key).then((detail) => { if (current) setFull({ detail, at: skill.updated_at }); }, () => { if (current) setOpen(false); });
    return () => { current = false; };
  }, [open, text, skill.key, skill.updated_at]);
  async function run<T>(kind: "save" | "load" | "remove", action: () => Promise<T>): Promise<T | undefined> {
    setBusy(kind); setError("");
    try { return await action(); }
    catch (failure) { setError(message(failure)); return undefined; }
    finally { setBusy(""); }
  }
  /** The full text of the skill, read once and kept. */
  async function loaded(): Promise<Skill | undefined> {
    if (text) return text;
    const detail = await run("load", () => fetchSkill(skill.key));
    if (detail) setFull({ detail, at: skill.updated_at });
    return detail;
  }
  async function toggle(next: boolean) {
    const saved = await run("save", () => changeSkill(skill.key, { enabled: next }));
    if (saved) skillsStore.apply(saved);
  }
  async function view() {
    if (!open && !(await loaded())) return;
    setOpen((value) => !value);
  }
  async function download() {
    const detail = await loaded();
    if (!detail) return;
    const url = URL.createObjectURL(new Blob([formatSkillMarkdown({ name: detail.name, description: detail.description, instructions: detail.instructions ?? "" })], { type: "text/markdown" }));
    const link = document.createElement("a");
    link.href = url; link.download = `${detail.name}.md`;
    document.body.append(link); link.click(); link.remove();
    URL.revokeObjectURL(url);
  }
  async function remove() {
    const done = await run("remove", async () => { await removeSkill(skill.key); return true; });
    if (done) skillsStore.remove(skill.key); else setConfirming(false);
  }
  return <li className="space-y-3 py-4" data-skill={skill.key}>
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <p className="break-all font-mono text-sm font-medium">/{skill.name}</p>
        <p className="text-sm leading-6 text-muted-foreground">{skill.description}</p>
      </div>
      <div className="shrink-0 pt-0.5"><Switch checked={skill.enabled} label={`${skill.name}: ${skill.enabled ? "on" : "off"}`} disabled={busy !== ""} onChange={(next) => void toggle(next)} /></div>
    </div>
    <div className="-ml-2 flex flex-wrap items-center gap-x-1">
      <button type="button" className={actionClass} aria-expanded={open} aria-controls={`${id}-text`} disabled={busy !== ""} onClick={() => void view()}><ChevronDown className={`size-4 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`} aria-hidden="true" />{busy === "load" ? "Loading…" : "Instructions"}</button>
      {custom ? <button type="button" className={actionClass} disabled={busy !== ""} onClick={async () => { const detail = await loaded(); if (detail) onEdit?.(detail); }}><Pencil className="size-3.5" aria-hidden="true" />Edit</button> : null}
      {!custom ? <button type="button" className={actionClass} disabled={busy !== ""} onClick={async () => { const detail = await loaded(); if (detail) onCopy?.(detail); }}><Plus className="size-3.5" aria-hidden="true" />Copy to my skills</button> : null}
      <button type="button" className={actionClass} disabled={busy !== ""} onClick={() => void download()}><Download className="size-3.5" aria-hidden="true" />Export</button>
      <span className="flex-1" />
      {custom ? (confirming ? <span className="flex items-center gap-1 text-sm"><span className="hidden text-muted-foreground sm:inline">Delete /{skill.name}?</span>
        <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" onClick={() => setConfirming(false)} disabled={busy !== ""}>Keep</Button>
        <Button type="button" variant="destructive" className="min-h-10 sm:min-h-8" onClick={() => void remove()} disabled={busy !== ""}>{busy === "remove" ? "Deleting…" : "Delete"}</Button></span>
        : <button type="button" className={actionClass} disabled={busy !== ""} onClick={() => setConfirming(true)}><Trash2 className="size-3.5" aria-hidden="true" />Delete</button>) : null}
    </div>
    {open && text ? <pre id={`${id}-text`} tabIndex={0} aria-label={`Instructions of ${skill.name}`} className="max-h-80 min-w-0 overflow-auto overscroll-contain whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 font-mono text-[12.5px] leading-5 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">{text.instructions}</pre> : null}
    {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
  </li>;
}

type Editing = { existing: Skill | null; initial: Partial<SkillFields> };

export function SkillsSettings() {
  const { status, overview, error } = useSkills();
  const [editing, setEditing] = useState<Editing | null>(null);
  const [importError, setImportError] = useState("");
  const file = useRef<HTMLInputElement>(null);
  async function importFile(chosen: File | undefined) {
    setImportError("");
    if (!chosen) return;
    if (chosen.size > MAX_IMPORT_BYTES) { setImportError("That file is too large to be a skill."); return; }
    try {
      const parsed = parseSkillMarkdown(await chosen.text());
      setEditing({ existing: null, initial: { name: parsed.name ?? "", description: parsed.description ?? "", instructions: parsed.instructions } });
    } catch { setImportError("That file could not be read."); }
    finally { if (file.current) file.current.value = ""; }
  }
  const custom = overview?.custom ?? [];
  const full = !!overview && custom.length >= overview.max_custom;
  return <section className={sectionClass} aria-labelledby="skills-title" aria-busy={status === "loading" && !overview}>
    <SectionHeader id="skills-title" title="Skills" description={<>Skills are playbooks the agent follows for a kind of work. It loads one when your request fits its description, or when you start a message with <code className="rounded bg-muted px-1 font-mono text-[0.85em]">/skill-name</code>. Add your own, or switch off the built-in ones you do not want.</>}
      action={<Button variant="ghost" size="icon" aria-label="Refresh skills" disabled={status === "loading"} onClick={() => skillsStore.reload()}><RefreshCw className={status === "loading" && overview ? "motion-safe:animate-spin" : ""} /></Button>} />
    {error && !overview ? <p role="alert" className="text-sm text-destructive">{error} <button type="button" className={linkClass} onClick={() => skillsStore.reload()}>Try again</button></p> : null}
    {!overview && !error ? <div role="status" className="space-y-4 border-y py-5"><span className="sr-only">Loading skills…</span><div className="h-5 w-48 animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-4 w-full max-w-md animate-pulse rounded bg-muted motion-reduce:animate-none" /></div> : null}
    {overview && !overview.enabled ? <p role="status" className="rounded-lg border px-3 py-2 text-sm leading-6">Skills are turned off on this server. The operator can turn them on with SKILLS_ENABLED.</p> : null}
    {overview?.enabled ? <>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className={subheadingClass}>Your skills<span className="ml-1.5 font-normal text-muted-foreground">{custom.length}/{overview.max_custom}</span></h3>
          {!editing ? <div className="flex items-center gap-1">
            <input ref={file} type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" className="hidden" tabIndex={-1} aria-label="Import a SKILL.md file" onChange={(event) => void importFile(event.target.files?.[0])} />
            <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" disabled={full} onClick={() => file.current?.click()}><Upload className="size-3.5" aria-hidden="true" />Import</Button>
            <Button type="button" variant="outline" className="min-h-10 sm:min-h-8" disabled={full} onClick={() => setEditing({ existing: null, initial: {} })}><Plus className="size-3.5" aria-hidden="true" />New skill</Button></div> : null}</div>
        {importError ? <p role="alert" className="text-sm text-destructive">{importError}</p> : null}
        {full ? <p className="text-sm leading-6 text-muted-foreground">You have the most skills allowed. Delete one to add another.</p> : null}
        {editing ? <SkillForm key={editing.existing?.key ?? `new-${editing.initial.name ?? ""}`} existing={editing.existing} initial={editing.initial} onClose={() => setEditing(null)} /> : null}
        {custom.length ? <ul className="divide-y border-y">{custom.map((skill) => <SkillRow key={skill.key} skill={skill}
          onEdit={(detail) => setEditing({ existing: detail, initial: { name: detail.name, description: detail.description, instructions: detail.instructions } })} />)}</ul>
          : !editing ? <p className="text-sm leading-6 text-muted-foreground">You have not added any skills yet. Write one, import a SKILL.md, or copy a built-in skill below and change it.</p> : null}
      </div>
      <div className={subsectionClass}>
        <h3 className={subheadingClass}>Built in<span className="ml-1.5 font-normal text-muted-foreground">{overview.builtin.length}</span></h3>
        <ul className="divide-y border-y">{overview.builtin.map((skill) => <SkillRow key={skill.key} skill={skill}
          onCopy={(detail) => { if (full) return; setEditing({ existing: null, initial: { name: copyName(detail.name), description: detail.description, instructions: detail.instructions } }); window.scrollTo({ top: 0, behavior: "smooth" }); }} />)}</ul>
        <p className="max-w-prose text-xs leading-5 text-muted-foreground">Built-in skills ship with AgentForge and are the same for everyone. Switch one off to stop the agent using it, or copy it to your own skills to change it.</p>
      </div>
    </> : null}
  </section>;
}
