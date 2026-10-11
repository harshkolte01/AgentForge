"use client";
import { useEffect, useState } from "react";
import { approvalModes, useApprovalMode } from "@/features/sessions/approval-mode-picker";
import { choiceClass, Fact, Facts, Group, iconTileClass, radioClass, sectionClass, SectionHeader, SkeletonRows } from "@/features/settings/settings-ui";
import type { ApprovalMode } from "@/lib/activity/approvals";
import { formatTokens, type ContextStatus } from "@/lib/context";
import { api, message } from "@/lib/task-api";
import type { WorkspaceConfiguration } from "@/lib/workspace-api";

/** Exactly what each mode means, in more words than the composer's menu has room for. */
const explanations: Record<ApprovalMode, string> = {
  ask: "Before it runs a command, writes or edits a file, or opens a pull request, the agent stops and shows you exactly what it will do. Reading files and searching never ask.",
  auto: "The agent runs commands, changes files and opens pull requests without stopping to ask.",
};

type Model = { model: string | null; provider: string | null; window: number | null; output: number | null };

function useModel() {
  const [model, setModel] = useState<Model | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    void Promise.allSettled([api<WorkspaceConfiguration>("/configuration", { signal }), api<ContextStatus>("/context", { signal })]).then(([config, context]) => {
      if (signal.aborted) return;
      if (config.status === "rejected" && context.status === "rejected") { setError(message(config.reason)); return; }
      const c = config.status === "fulfilled" ? config.value : null;
      const x = context.status === "fulfilled" ? context.value : null;
      setModel({ model: c?.configured_model ?? c?.model ?? x?.model ?? null, provider: c?.configured_provider ?? c?.provider ?? x?.provider ?? null, window: x?.window_tokens ?? null, output: x?.max_output_tokens ?? null });
    });
    return () => controller.abort();
  }, []);
  return { model, error };
}

export function AgentSettings() {
  const { mode, status, choose, retry } = useApprovalMode();
  const [saveError, setSaveError] = useState("");
  const { model, error } = useModel();
  const current = mode ?? "ask";
  function pick(next: ApprovalMode) {
    setSaveError("");
    if (next !== mode) void choose(next).catch((failure) => setSaveError(message(failure)));
  }
  return <section className={sectionClass} aria-labelledby="agent-title">
    <SectionHeader id="agent-title" title="Agent" description="How the agent works for you in every conversation." />
    <fieldset className="space-y-3" disabled={status === "idle" || (status === "loading" && mode === null)}>
      <legend className="mb-3 text-sm font-medium">Approvals</legend>
      <div className="grid gap-3">
        {approvalModes.map(({ id, label, Icon }) => <label key={id} className={choiceClass}>
          <input type="radio" name="approval-mode" value={id} checked={current === id} onChange={() => pick(id)} className={`${radioClass} mt-2.5`} aria-describedby={`approval-${id}-hint`} />
          <span className={`${iconTileClass} bg-background group-has-[:checked]:text-foreground`}><Icon aria-hidden="true" /></span>
          <span className="min-w-0 space-y-1"><span className="block text-sm font-medium leading-9">{label}</span><span id={`approval-${id}-hint`} className="-mt-1.5 block text-sm leading-6 text-muted-foreground">{explanations[id]}</span></span>
        </label>)}
      </div>
      <p className="min-h-5 text-[0.8125rem] leading-5" role="status">
        {saveError ? <span className="text-destructive">{saveError}</span>
          : status === "error" ? <span className="text-muted-foreground">Your saved choice couldn’t be read, so “Ask me” applies. <button type="button" onClick={retry} className="rounded underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">Try again</button></span>
            : status === "saving" ? <span className="text-muted-foreground">Saving…</span> : <span className="text-muted-foreground">Applies to every conversation, on every device.</span>}
      </p>
    </fieldset>
    <Group title="Model" note="The model is set by the workspace operator.">
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p>
        : !model ? <SkeletonRows count={3} label="Loading the model…" />
          : <Facts>
            <Fact label="Model"><span className="font-medium">{model.model ?? "Not configured"}</span></Fact>
            {model.provider ? <Fact label="Provider">{model.provider}</Fact> : null}
            {model.window ? <Fact label="Context window"><span className="tabular-nums">{formatTokens(model.window)} tokens{model.output ? <span className="text-muted-foreground"> · up to {formatTokens(model.output)} per reply</span> : null}</span></Fact> : null}
          </Facts>}
    </Group>
  </section>;
}
