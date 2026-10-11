"use client";
import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Download, Pencil, Plus, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { inputClass } from "@/components/session-boundary";
import { AddMenu, EmptyLine, ListRow, ListSection, MenuItem, RefreshButton, RowList, RowSkeleton, SearchField, Toolbar } from "@/features/plugins/catalog-ui";
import { skillsStore, useSkills } from "@/features/skills/use-skills";
import { focusClass, Switch } from "@/features/settings/settings-ui";
import {
  changeSkill, copyName, createSkill, DESCRIPTION_MAX, fetchSkill, formatSkillMarkdown, INSTRUCTIONS_MAX, nameProblem, parseSkillMarkdown,
  removeSkill, type Skill, type SkillFields,
} from "@/lib/skills-api";
import { message } from "@/lib/task-api";

const linkClass = `rounded underline underline-offset-4 ${focusClass}`;
const footerButton = `inline-flex min-h-10 items-center gap-1.5 rounded-md px-2.5 text-[0.8125rem] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none sm:min-h-8 ${focusClass}`;
const MAX_IMPORT_BYTES = 100_000;

/** The "/" tile every skill carries: the character that asks for it. */
function SlashTile({ size = "md" }: { size?: "md" | "lg" }) {
  return <span aria-hidden="true" className={`grid shrink-0 select-none place-items-center border bg-card font-mono text-muted-foreground ${size === "lg" ? "size-11 rounded-xl text-lg" : "size-11 rounded-xl text-base"}`}>/</span>;
}

/** Writes or edits one of the person's own skills, in a dialog. `existing` is the skill being edited (with its instructions); none means a new one. */
function SkillDialog({ existing, initial, onClose }: { existing: Skill | null; initial: Partial<SkillFields>; onClose: () => void }) {
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
  const title = existing ? `Edit /${existing.name}` : "New skill";
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent className="sm:w-[min(42rem,calc(100vw-2rem))]">
      <form onSubmit={submit} className="flex min-h-0 flex-col" aria-label={existing ? `Edit ${existing.name}` : "New skill"} aria-busy={busy}>
        <div className="flex items-start gap-3.5 px-5 pb-3 pt-5"><SlashTile /><div className="min-w-0 space-y-0.5"><DialogTitle>{title}</DialogTitle><DialogDescription>A playbook the agent follows for a kind of work.</DialogDescription></div></div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-3">
          <div className="space-y-1.5">
            <label htmlFor={`${id}-name`} className="text-sm font-medium">Name</label>
            <div className="flex items-center gap-2"><span aria-hidden="true" className="font-mono text-sm text-muted-foreground">/</span>
              <input id={`${id}-name`} aria-describedby={`${id}-name-hint`} aria-invalid={!!problem} className={`${inputClass} font-mono`} value={name} onChange={(event) => setName(event.target.value)} required maxLength={40} placeholder="review-contract" disabled={busy} autoComplete="off" autoCapitalize="none" spellCheck={false} /></div>
            <p id={`${id}-name-hint`} className={`text-xs leading-5 ${problem ? "text-destructive" : "text-muted-foreground"}`}>{problem ?? "What you type after “/” to ask for it, and what the agent loads it by."}</p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor={`${id}-description`} className="text-sm font-medium">Description</label>
            <textarea id={`${id}-description`} aria-describedby={`${id}-description-hint`} className={`${inputClass} min-h-16 resize-y`} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} required maxLength={DESCRIPTION_MAX} placeholder="Review a contract for risky clauses. Use when asked to check, summarise or compare contract terms." disabled={busy} />
            <p id={`${id}-description-hint`} className="flex justify-between gap-3 text-xs leading-5 text-muted-foreground"><span>Say what it does and when to use it. It is all the agent sees before deciding to load the skill.</span><span className="shrink-0 tabular-nums">{description.length}/{DESCRIPTION_MAX}</span></p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor={`${id}-instructions`} className="text-sm font-medium">Instructions</label>
            <textarea id={`${id}-instructions`} aria-describedby={`${id}-instructions-hint`} className={`${inputClass} min-h-44 resize-y font-mono text-[13px] leading-5`} rows={9} value={instructions} onChange={(event) => setInstructions(event.target.value)} required placeholder={"# Review a contract\n\n1. Read the whole document first.\n2. List every clause that…"} disabled={busy} spellCheck={false} />
            <p id={`${id}-instructions-hint`} className="flex justify-between gap-3 text-xs leading-5 text-muted-foreground"><span>Steps for the agent to follow, in Markdown. They apply within the platform rules and cannot give it new permissions.</span><span className={`shrink-0 tabular-nums ${instructions.length > INSTRUCTIONS_MAX ? "text-destructive" : ""}`}>{instructions.length.toLocaleString()}/{INSTRUCTIONS_MAX.toLocaleString()}</span></p>
          </div>
          {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
        </div>
        <div className="flex flex-wrap justify-end gap-2 px-5 pb-5 pt-3">
          <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" className="min-h-10 sm:min-h-8" disabled={busy || !!problem}>{busy ? "Saving…" : existing ? "Save changes" : "Add skill"}</Button>
        </div>
      </form>
    </DialogContent>
  </Dialog>;
}

/** One skill in the list: a row with its switch; opening it shows the instructions and what can be done with the skill. */
function SkillItem({ skill, onEdit, onCopy }: { skill: Skill; onEdit?: (full: Skill) => void; onCopy?: (full: Skill) => void }) {
  const [viewing, setViewing] = useState(false);
  const [full, setFull] = useState<{ detail: Skill; at: string | null } | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "load" | "remove">("");
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const custom = skill.source === "custom";
  // The text read earlier belongs to the version it was read at: a saved change (its `updated_at` moves) makes it stale.
  const text = full && full.at === skill.updated_at ? full.detail : null;
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
  return <>
    <ListRow rowProps={{ "data-skill": skill.key }} leading={<SlashTile />} title={<><span aria-hidden="true" className="text-muted-foreground">/</span>{skill.name}</>}
      subtitle={error && !viewing ? <span className="text-destructive" role="alert">{error}</span> : skill.description}
      onOpen={async () => { if (await loaded()) setViewing(true); }} openLabel={`Open /${skill.name}`}
      trailing={<Switch checked={skill.enabled} label={`${skill.name}: ${skill.enabled ? "on" : "off"}`} disabled={busy === "save" || busy === "remove"} onChange={(next) => void toggle(next)} />} />
    {viewing && text ? <Dialog open onOpenChange={(open) => { if (!open && busy === "") { setViewing(false); setConfirming(false); } }}>
      <DialogContent className="sm:w-[min(42rem,calc(100vw-2rem))]">
        <div className="flex items-start gap-3.5 px-5 pb-3 pt-5"><SlashTile /><div className="min-w-0 space-y-0.5"><DialogTitle className="break-all">/{text.name}</DialogTitle><DialogDescription>{text.description}</DialogDescription></div></div>
        <pre tabIndex={0} aria-label={`Instructions of ${text.name}`} className="mx-5 my-2 min-h-0 flex-1 overflow-auto overscroll-contain whitespace-pre-wrap break-words rounded-xl border bg-muted/30 px-4 py-3 font-mono text-[12.5px] leading-5 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">{text.instructions ?? ""}</pre>
        {error ? <p role="alert" className="px-5 pb-1 text-sm leading-6 text-destructive">{error}</p> : null}
        <div className="flex flex-wrap items-center gap-x-0.5 gap-y-1 border-t px-3 py-2.5 sm:px-4">
          {custom ? (confirming ? <span className="mr-auto flex items-center gap-1 text-sm"><span className="hidden text-muted-foreground sm:inline">Delete /{skill.name}?</span>
            <Button type="button" variant="ghost" className="min-h-10 sm:min-h-8" onClick={() => setConfirming(false)} disabled={busy !== ""}>Keep</Button>
            <Button type="button" variant="destructive" className="min-h-10 sm:min-h-8" onClick={() => void remove()} disabled={busy !== ""}>{busy === "remove" ? "Deleting…" : "Delete"}</Button></span>
            : <button type="button" className={`${footerButton} mr-auto hover:text-destructive`} disabled={busy !== ""} onClick={() => setConfirming(true)}>Delete</button>) : <span className="mr-auto" />}
          <button type="button" className={footerButton} disabled={busy !== ""} onClick={() => void download()}><Download className="size-3.5" aria-hidden="true" />Export</button>
          {custom ? <button type="button" className={footerButton} disabled={busy !== ""} onClick={() => { setViewing(false); onEdit?.(text); }}><Pencil className="size-3.5" aria-hidden="true" />Edit</button> : null}
          {!custom ? <button type="button" className={footerButton} disabled={busy !== ""} onClick={() => { setViewing(false); onCopy?.(text); }}><Plus className="size-3.5" aria-hidden="true" />Copy to my skills</button> : null}
          <Button type="button" className="ml-1 min-h-10 sm:min-h-8" onClick={() => { setViewing(false); setConfirming(false); }} disabled={busy !== ""}>Done</Button>
        </div>
      </DialogContent>
    </Dialog> : null}
  </>;
}

type Editing = { existing: Skill | null; initial: Partial<SkillFields> };

/** The person's own skills and the built-in ones: shared by the Plugins page (with its `tabs`) and Settings → Skills. */
export function SkillsPanel({ tabs }: { tabs?: ReactNode }) {
  const { status, overview, error } = useSkills();
  const [query, setQuery] = useState("");
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
  const enabled = !!overview?.enabled;
  const custom = overview?.custom ?? [];
  const builtin = overview?.builtin ?? [];
  const full = !!overview && custom.length >= overview.max_custom;
  const needle = query.trim().toLowerCase();
  const matches = (skill: Skill) => !needle || skill.name.toLowerCase().includes(needle) || skill.description.toLowerCase().includes(needle);
  const shownCustom = custom.filter(matches);
  const shownBuiltin = builtin.filter(matches);
  const nothing = enabled && !!needle && !shownCustom.length && !shownBuiltin.length;
  return <div className="space-y-9" aria-busy={status === "loading" && !overview}>
    <input ref={file} type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" className="hidden" tabIndex={-1} aria-label="Import a SKILL.md file" onChange={(event) => void importFile(event.target.files?.[0])} />
    <Toolbar tabs={tabs}>
      <SearchField value={query} onChange={setQuery} label="Search skills" />
      <RefreshButton label="Refresh skills" busy={status === "loading"} onClick={() => skillsStore.reload()} />
      <AddMenu disabled={!enabled}>{(close) => <>
        <MenuItem disabled={full} icon={<Plus className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />} onClick={() => { close(); setEditing({ existing: null, initial: {} }); }}>New skill</MenuItem>
        <MenuItem disabled={full} icon={<Upload className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />} hint="From a SKILL.md file" onClick={() => { close(); file.current?.click(); }}>Import</MenuItem>
        {full && overview ? <p className="px-2.5 py-2 text-xs leading-5 text-muted-foreground">You have the most skills allowed ({overview.max_custom}). Delete one to add another.</p> : null}
      </>}</AddMenu>
    </Toolbar>
    {error && !overview ? <p role="alert" className="text-sm text-destructive">{error} <button type="button" className={linkClass} onClick={() => skillsStore.reload()}>Try again</button></p> : null}
    {importError ? <p role="alert" className="text-sm text-destructive">{importError}</p> : null}
    {!overview && !error ? <RowSkeleton label="Loading skills…" /> : null}
    {overview && !overview.enabled ? <p role="status" className="text-sm leading-6 text-muted-foreground">Skills are turned off on this server. The operator can turn them on with SKILLS_ENABLED.</p> : null}
    {enabled && overview ? <>
      {nothing ? <p role="status" className="text-sm text-muted-foreground">No skills match “{query.trim()}”.</p> : null}
      {!needle || shownCustom.length ? <ListSection title="Your skills" count={`${custom.length}/${overview.max_custom}`} note={full ? "You have the most skills allowed. Delete one to add another." : undefined}>
        {shownCustom.length ? <RowList label="Your skills">{shownCustom.map((skill) => <SkillItem key={skill.key} skill={skill}
          onEdit={(detail) => setEditing({ existing: detail, initial: { name: detail.name, description: detail.description, instructions: detail.instructions } })} />)}</RowList>
          : <EmptyLine icon={<span aria-hidden="true" className="font-mono text-base">/</span>}>You have not added any skills yet. Write one, import a SKILL.md, or open a built-in skill below and copy it to change it.</EmptyLine>}
      </ListSection> : null}
      {!needle || shownBuiltin.length ? <ListSection title="Built in" count={builtin.length}>
        <RowList label="Built-in skills">{shownBuiltin.map((skill) => <SkillItem key={skill.key} skill={skill}
          onCopy={(detail) => { if (full) return; setEditing({ existing: null, initial: { name: copyName(detail.name), description: detail.description, instructions: detail.instructions } }); }} />)}</RowList>
        <p className="max-w-prose pt-1 text-xs leading-5 text-muted-foreground">Built-in skills ship with AgentForge and are the same for everyone. Switch one off to stop the agent using it, or copy it to your own skills to change it.</p>
      </ListSection> : null}
    </> : null}
    {editing ? <SkillDialog key={editing.existing?.key ?? `new-${editing.initial.name ?? ""}`} existing={editing.existing} initial={editing.initial} onClose={() => setEditing(null)} /> : null}
  </div>;
}
